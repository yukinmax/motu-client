import asyncio
import json
import math
import logging
import motu
import time

tmp_mapping = {
    '13': 'mix/chan/0/matrix/aux/0/send',
    '14': 'mix/chan/2/matrix/aux/0/send',
    '15': 'mix/chan/4/matrix/aux/0/send',
}

raw_db_range_mapping = (
    (0, -math.inf),
    ((1, 5), (-120, -60)),
    ((5, 125), (-60, -30)),
    ((125, 1000), (-30, 12))
)


class RawPanel():
    def __init__(self, host, port=9923, mode='ASCII', delay=0.001):
        self.mode = mode
        self.host = str(host)
        self.port = int(port)
        self.reader = None
        self.writer = None
        self.sys_stat = None
        self.delay = delay
        self.info = {
            "model": None,
            "serial": None,
            "version": None,
            "name": None,
            "platform": None,
            "bluePillReady": None,
            "panelType": None,
            "support": None,
            "isSleeping": None,
            "EnvironmenalHealth": None,
        }
        self.commands = {
            "SysStat": self._update_sys_stat,
            "_model": self._update_model,
            "_serial": self._update_serial,
            "_version": self._update_version,
            "_name": self._update_name,
            "_platform": self._update_platform,
            "_bluePillReady": self._update_bluePillReady,
            "_panelType": self._update_panelType,
            "_support": self._update_support,
            "_isSleeping": self._update_isSleeping,
            "EnvironmentalHealth": self._update_EnvironmentalHealth,
            "HWC": self._hardware_change
        }
        self.hw_change_timings = {}
        self.ds = None
        self.ms = None

    async def _update_sys_stat(self, value):
        self.sys_stat = value
        logging.debug("Updated System Stats")

    async def _update_model(self, value):
        self.info['model'] = value

    async def _update_serial(self, value):
        self.info['serial'] = value

    async def _update_version(self, value):
        self.info['version'] = value

    async def _update_name(self, value):
        self.info['name'] = value

    async def _update_platform(self, value):
        self.info['platform'] = value

    async def _update_bluePillReady(self, value):
        self.info['bluePillReady'] = value

    async def _update_panelType(self, value):
        self.info['panelType'] = value

    async def _update_support(self, value):
        self.info['support'] = value

    async def _update_isSleeping(self, value):
        self.info['isSleeping'] = value

    async def _update_EnvironmentalHealth(self, value):
        self.info['EnvironmentalHealth'] = value

    async def _hardware_change(self, hwid, value):
        t = time.monotonic()
        change_hist = self.hw_change_timings.setdefault(hwid, 0)
        if t - change_hist < self.delay:
            return
        try:
            s, v = value.split(':')
        except ValueError:
            v = value
        logging.debug("hwid {} is set to {}".format(hwid, v))
        try:
            v = int(v)
        except ValueError:
            pass
        else:
            v = await motu.db_from_raw(v, raw_db_range_mapping)
            v = await motu.level_from_db(v)
        finally:
            try:
                path = tmp_mapping[hwid]
            except KeyError:
                logging.info("hwid {} is not mapped".format(hwid))
            else:
                if self.ds:
                    await self.ds.set(path, v)
        self.hw_change_timings[hwid] = time.monotonic()

    def set_ds(self, datastore):
        self.ds = datastore

    def set_ms(self, meters):
        self.ms = meters

    async def connect(self):
        logging.info("Connecting to {}:{}...".format(self.host,
                                                     self.port))
        self.reader, self.writer = await asyncio.open_connection(
            self.host,
            self.port
        )
        logging.info("Connected to {}:{}.".format(self.host,
                                                  self.port))
        await self.send('list')
        await asyncio.sleep(1)
        logging.info("Raw Panel {} initialized".format(self.host))

    async def disconnect(self):
        logging.info("Closing connection to {}:{}...".format(self.host,
                                                             self.port))
        self.writer.close()
        await self.writer.wait_closed()

    async def handle(self, request):
        key, value = request.split('=')
        try:
            command, hwid = key.split('#')
        except ValueError:
            command = key
            params = (value,)
        else:
            params = hwid, value
        try:
            await self.commands[command](*params)
        except KeyError:
            logging.warn(request)

    async def handle_requests(self):
        while True:
            try:
                r = await self.receive()
                await self.handle(r)
            except asyncio.CancelledError:
                break

    async def send(self, message):
        self.writer.write('{}\n'.format(message).encode('ascii'))
        return await self.writer.drain()

    async def receive(self):
        record = (await self.reader.readline()).decode().strip()
        logging.debug(record)
        return record

    async def move_fader(self, hwid, value):
        msg = {
            "HWCIDs": [hwid],
            "HWCExtended": {
                "Interpretation": 5
            }
        }
        if value:
            msg["HWCExtended"]["Value"] = value
        await self.send(json.dumps(msg))
