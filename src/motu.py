import requests as req
import json
import asyncio
import random
import math
import logging


level_range = (0, 10 ** (12 / 20))


async def request(url, params=None, etag=None, method='GET', data=None,
                  retries=None, retry_interval_sec=10):
    headers = {}
    attempt = 0
    if etag:
        headers['If-None-Match'] = etag
    if method == 'PATCH':
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    while True:
        attempt += 1
        try:
            r = await asyncio.to_thread(req.request,
                                        method,
                                        url,
                                        params=params,
                                        headers=headers,
                                        data=data)
        except(req.exceptions.ConnectionError):
            logging.warn("Error connecting to {}".format(url))
            if retries is not None and attempt > retries:
                logging.error(("Maximum retries reached "
                               "connecting to {}".format(url)))
                r = {
                    'status_code': 503,
                    'reason': "Service Unavailable"
                }
                break
            else:
                await asyncio.sleep(retry_interval_sec)
        else:
            break
    if r.status_code in (200, 204):
        return r
    elif r.status_code != 304:
        print('Error code {} - {}'.format(r.status_code, r.reason))
    else:
        pass
    return None


async def limit_to_range(value, minimum, maximum):
    return max(min(value, maximum), minimum)


async def level_to_db(value):
    if value == 0:
        return -math.inf
    else:
        return round(20 * math.log10(value), 2)


async def level_from_db(db_value):
    db_value = float(db_value)
    if db_value > 12:
        db_value = 12
    elif db_value < -120:
        db_value = -math.inf
    return 10 ** (db_value / 20)


async def db_from_raw(value, range_mapping, reverse=False):
    from_index = int(reverse)
    to_index = int(not(reverse))
    if reverse:
        type_conversion = int
    else:
        type_conversion = float
    try:
        from_min_abs = range_mapping[0][from_index][0]
        to_min_abs = range_mapping[0][to_index][0]
    except TypeError:
        from_min_abs = range_mapping[0][from_index]
        to_min_abs = range_mapping[0][to_index]
    try:
        from_max_abs = range_mapping[-1][from_index][1]
        to_max_abs = range_mapping[-1][to_index][1]
    except TypeError:
        from_max_abs = range_mapping[-1][from_index]
        to_max_abs = range_mapping[-1][to_index]
    value = await limit_to_range(value, from_min_abs, from_max_abs)
    for i in range_mapping:
        try:
            from_min = i[from_index][0]
            from_max = i[from_index][1]
            to_min = i[to_index][0]
            to_max = i[to_index][1]
        except TypeError:
            if value == i[from_index]:
                return type_conversion(i[to_index])
        else:
            if from_min <= value < from_max:
                break
    cr = (to_max - to_min) / (from_max - from_min)
    out_value = ((value - from_min) * cr) + to_min
    out_value = await limit_to_range(out_value, to_min_abs, to_max_abs)
    return type_conversion(out_value)


async def dict_diff(d_old, d_new):
    return dict(set(d_new.items()) - set(d_old.items()))


async def dict_values_to_tuples(d):
    d_new = {}
    for k, v in d.items():
        try:
            d_new[k] = tuple(v)
        except TypeError:
            d_new[k] = v
    return d_new


def generate_client_id():
    return random.getrandbits(32)


class Store():
    def __init__(self, hostname="ultralite-avb.local"):
        self.hostname = hostname
        self.base_path = ''
        self.refresh_params = None
        self.etag = None
        self.client_id = None
        self.data = {}
        self.change_handler = None

    async def refresh(self, diff_check=False, handle_changes=True):
        url = 'http://{}/{}'.format(
            self.hostname,
            self.base_path
        )
        params = {}
        if self.refresh_params:
            params.update(self.refresh_params)
        params['client'] = self.client_id
        response = await request(
            url=url,
            params=params,
            etag=self.etag
        )
        if response:
            if diff_check:
                new_data = await dict_values_to_tuples(response.json())
                data_diff = await dict_diff(self.data, new_data)
            else:
                data_diff = await dict_values_to_tuples(response.json())
            self.etag = response.headers['ETag']
            if data_diff:
                self.data.update(data_diff)
                logging.debug("Modified: {} -> {}".format(self.base_path,
                                                          data_diff))
                if self.change_handler and handle_changes:
                    await self.change_handler(data_diff)
                return data_diff
        else:
            logging.debug("Not modified: {}".format(self.base_path))

    async def get(self, path):
        value = self.data[path]
        return value

    async def poll(self, diff_check=False, handle_changes=True):
        logging.info("Polling MOTU {} ({})...".format(self.base_path,
                                                      self.hostname))
        while True:
            try:
                await self.refresh(diff_check=diff_check,
                                   handle_changes=handle_changes)
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break

    def set_change_handler(self, handler):
        self.change_handler = handler


class DataStore(Store):
    def __init__(self, hostname=None):
        if hostname:
            super().__init__(hostname)
        else:
            super().__init__()
        self.base_path = 'datastore'
        self.client_id = generate_client_id()

    async def set(self, path, value):
        url = 'http://{}/{}'.format(
            self.hostname,
            self.base_path
        )
        params = {
            'client': self.client_id
        }
        data = 'json={}'.format(json.dumps({path: value})).encode()
        response = await request(
            url=url,
            params=params,
            method='PATCH',
            data=data
        )
        if response:
            data_diff = {path: value}
            self.data.update(data_diff)
            logging.debug("Modified: {} -> {}".format(self.base_path,
                                                      data_diff))
            if self.change_handler:
                await self.change_handler({path: value})
        return response

    async def toggle(self, path):
        try:
            s = await self.get(path)
        except KeyError:
            return "FAILURE"
        j = float(not(s))
        r = await self.set(path, j)
        if r.status_code == 204:
            return j
        else:
            print("FAILURE")
            return "FAILURE"


class Meters(Store):
    def __init__(self, hostname=None):
        if hostname:
            super().__init__(hostname)
        else:
            super().__init__()
        self.base_path = 'meters'
        self.refresh_params = {
            'meters': 'mix/level'
        }
        self.client_id = generate_client_id()

    async def update_peaks(self):
        filtered_data = {k: v for k, v in self.data.items()
                         if not k.endswith('peaks')}
        peaks = {
            'mix/level/peaks':
            tuple([max(values) for values in zip(*filtered_data.values())])
        }
        self.data.update(peaks)
        logging.debug("Modified: {} -> {}".format(self.base_path, peaks))
        return peaks

    async def refresh(self, diff_check=True, handle_changes=True):
        data_diff = await super().refresh(diff_check=diff_check,
                                          handle_changes=False)
        if data_diff:
            data_diff.update(await self.update_peaks())
            if self.change_handler and handle_changes:
                await self.change_handler(data_diff)

    async def poll(self, diff_check=True, handle_changes=True):
        logging.info("Polling MOTU {} ({})...".format(self.base_path,
                                                      self.hostname))
        while True:
            try:
                await self.refresh(diff_check=diff_check,
                                   handle_changes=handle_changes)
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break
