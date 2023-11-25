import requests as req
import json
import asyncio
import random


async def request(url, params=None, etag=None, method='GET', data=None):
    headers = {}
    if etag:
        headers['If-None-Match'] = etag
    if method == 'PATCH':
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    r = await asyncio.to_thread(req.request,
                                method,
                                url,
                                params=params,
                                headers=headers,
                                data=data)
    if r.status_code in (200, 204):
        return r
    elif r.status_code != 304:
        print('Error code {} - {}'.format(r.status_code, r.reason))
    else:
        pass
    return None


def nested_set(dic, keys, value):
    if len(keys) > 1:
        dd = dic.setdefault(keys.pop(0), {})
        nested_set(dd, keys, value)
    else:
        dic[keys.pop()] = value


def parse_response(json):
    d = {}
    for key in json:
        keys = key.split('/')
        try:
            nested_set(d, keys, {'value': json[key]})
        except TypeError:
            print(key)
    return d


def generate_client_id():
    return random.getrandbits(32)


async def poll_all():
    ds = DataStore()
    ms = Meters()
    await ds.refresh()
    await ms.refresh()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(ds.poll())
        tg.create_task(ms.poll())


class Store():
    def __init__(self, hostname="ultralite-avb.local"):
        self.hostname = hostname
        self.base_path = ''
        self.refresh_params = None
        self.etag = None
        self.client_id = None
        self.data = {}

    async def refresh(self):
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
            self.data.update(response.json())
            self.etag = response.headers['ETag']
            # print("Modified: {}".format(self.base_path))
        else:
            # print("Not Modified: {}".format(self.base_path))
            pass

    async def get(self, path):
        value = self.data[path]
        return value

    async def poll(self):
        while True:
            try:
                await self.refresh()
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break


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
            self.data[path] = value
            # print("Modified: {}".format(self.base_path))
        return response

    async def toggle(self, path):
        try:
            s = await self.get(path)
        except KeyError:
            return "FAILURE"
        j = abs(s - 1)
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
            'meters': 'mix/gate:mix/comp:mix/level:mix/leveler:ext/input'
        }
        self.client_id = generate_client_id()


if __name__ == '__main__':
    asyncio.run(poll_all())
