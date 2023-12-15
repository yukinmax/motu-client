import asyncio
import json
import math
import re
import logging
import motu
import time
from bidict import bidict

tmp_mapping = bidict({
    '13': 'mix/chan/0/matrix/aux/0/send',
    '14': 'mix/chan/2/matrix/aux/0/send',
    '15': 'mix/chan/4/matrix/aux/0/send',
    '61.4': 'mix/chan/0/matrix/mute',
    '64.4': 'mix/chan/2/matrix/mute',
    '67.4': 'mix/chan/4/matrix/mute',
})

feedback_map = {
    'mix/chan/0/matrix/mute': {
        'button': {
            'hwcid': 61,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/2/matrix/mute': {
        'button': {
            'hwcid': 64,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/4/matrix/mute': {
        'button': {
            'hwcid': 67,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/0/matrix/aux/0/send': {
        'fader': {
            'hwcid': 13,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 29,
            'text': {
                'formatting': 7,
                'title': 'Mic',
                'solid_header': True
            }
        }
    },
    'mix/chan/2/matrix/aux/0/send': {
        'fader': {
            'hwcid': 14,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 30,
            'text': {
                'formatting': 7,
                'title': 'PC',
                'solid_header': True
            }
        }
    },
    'mix/chan/4/matrix/aux/0/send': {
        'fader': {
            'hwcid': 15,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 31,
            'text': {
                'formatting': 7,
                'title': 'Music',
                'solid_header': True
            }
        }
    },
    'mix/level/0': {
        'display1': {
            'hwcid': 37,
            'channels': [0],
            'audio_meter': {
                'mono': True,
                'meter_type': 1,
            }
        },
        'display2': {
            'hwcid': 38,
            'channels': [2, 3],
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display3': {
            'hwcid': 39,
            'channels': [4, 5],
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        }
    }
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
        self.connected = False
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
        self.panel_map = {}
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
            "map": self._update_map,
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
        prev_state = self.info['isSleeping']
        new_state = bool(int(value))
        logging.info("Sleeping: {} -> {}".format(prev_state, new_state))
        self.info['isSleeping'] = new_state
        if not new_state and prev_state:
            await asyncio.sleep(1)
            await self.init_feedback()

    async def _update_EnvironmentalHealth(self, value):
        self.info['EnvironmentalHealth'] = value

    async def _update_map(self, value):
        k, v = value.split(":")
        self.panel_map[k] = v

    async def _hardware_change(self, hwcid, value):
        try:
            path = tmp_mapping[hwcid]
        except KeyError:
            logging.info("hwcid {} is not mapped".format(hwcid))
            return
        t = time.perf_counter()
        change_hist = self.hw_change_timings.setdefault(hwcid, 0)
        if t - change_hist < self.delay:
            return
        path_type = re.search(r"\w+$", path)[0]
        try:
            s, v = value.split(':')
        except ValueError:
            v = value
        logging.debug("hwcid {} is set to {}".format(hwcid, v))
        if path_type in ('send',):
            try:
                v = int(v)
            except ValueError:
                pass
            else:
                v = await motu.db_from_raw(v, raw_db_range_mapping)
                v = await motu.level_from_db(v)
            finally:
                if self.ds:
                    await self.ds.set(path, v)
        elif path_type in ('mute', 'solo'):
            if re.match(r"Down", v):
                if self.ds:
                    await self.ds.toggle(path)
        self.hw_change_timings[hwcid] = time.perf_counter()

    def set_ds(self, datastore):
        self.ds = datastore

    def set_ms(self, meters):
        self.ms = meters

    async def init_feedback(self):
        if not self.ds:
            logging.info("datastore should be set first")
            return
        if not self.ms:
            logging.info("meters should be set first")
            return
        logging.info("Initializing the panel feedback")
        dd = {}
        md = {}
        for path in feedback_map:
            try:
                v = await self.ds.get(path)
            except KeyError:
                try:
                    v = await self.ms.get(path)
                except KeyError:
                    logging.warning("Path {} is not available")
                    continue
                else:
                    md[path] = v
            else:
                dd[path] = v
        logging.debug("Init datastore feedback {}".format(dd))
        logging.debug("Init meters feedback {}".format(md))
        await self.process_data_feedback(dd)
        await self.process_meters_feedback(md)

    async def connect(self):
        logging.info("Connecting to {}:{}...".format(self.host,
                                                     self.port))
        self.reader, self.writer = await asyncio.open_connection(
            self.host,
            self.port
        )
        self.connected = True
        logging.info("Connected to {}:{}.".format(self.host,
                                                  self.port))
        hello_msg = {'FlowMessage': 'OutboundMessage_HELLO'}
        await self.send(hello_msg)
        await asyncio.sleep(1)
        logging.info("Raw Panel {} initialized".format(self.host))

    async def disconnect(self):
        logging.info("Closing connection to {}:{}...".format(self.host,
                                                             self.port))
        self.writer.close()
        await self.writer.wait_closed()
        self.connected = False

    async def handle_request(self, request):
        try:
            key, value = request.split('=')
        except ValueError:
            if len(request):
                logging.warn("Invalid request: {}".format(request))
            else:
                logging.error("Connection to {} lost".format(self.host))
                raise asyncio.CancelledError
                return
        try:
            command, hwcid = key.split('#')
        except ValueError:
            command = key
            params = (value,)
        else:
            params = hwcid, value
        try:
            await self.commands[command](*params)
        except KeyError:
            logging.warn(request)
            return

    async def handle_requests(self):
        while self.connected:
            try:
                r = await self.receive()
                await self.handle_request(r)
            except asyncio.CancelledError:
                await self.disconnect()
                break

    async def send(self, message):
        if not self.connected:
            return
        message = json.dumps(message, separators=(',', ':'))
        logging.debug(message)
        self.writer.write('{}\n'.format(message).encode('ascii'))
        try:
            await self.writer.drain()
        except ConnectionResetError:
            logging.warn("Some messages were not delivered")

    async def receive(self):
        record = (await self.reader.readline()).decode().strip()
        logging.debug(record)
        return record

    async def process_data_feedback(self, d):
        for k, v in d.items():
            try:
                mapping = feedback_map[k]
            except KeyError:
                logging.debug("path {} is not mapped".format(k))
                continue
            t = re.search(r"\w+$", k)[0]
            if t == 'send':
                db_value = await motu.level_to_db(float(v))
                raw_value = await motu.db_from_raw(
                    db_value,
                    raw_db_range_mapping,
                    reverse=True
                )
            for m in mapping:
                hwcid = mapping[m]['hwcid']
                msg = {}
                if m in ('button', 'fader'):
                    try:
                        mode = mapping[m]['mode']
                    except KeyError:
                        pass
                    else:
                        msg.update(await self._set_mode(hwcid, **mode))
                    try:
                        color = mapping[m]['color'].copy()
                    except KeyError:
                        pass
                    else:
                        for color_type, color_value in color.items():
                            try:
                                color[color_type] = (
                                    color_value[0] if v else color_value[1]
                                )
                            except TypeError:
                                pass
                        msg.update(await self._set_color(hwcid, **color))
                if m == 'fader':
                    msg.update(await self._move_fader(hwcid, raw_value))
                if m == 'display':
                    try:
                        txt = mapping[m]['text']
                    except KeyError:
                        txt = {}
                    msg.update(await self._set_text(hwcid,
                                                    text1=db_value,
                                                    **txt))
                await self.send(msg)

    async def process_meters_feedback(self, d):
        # Currently sends data for all meters even if only 1 meter data changed
        for k, v in d.items():
            try:
                mapping = feedback_map[k]
            except KeyError:
                logging.debug("path {} is not mapped".format(k))
                continue
            for m in mapping:
                hwcid = mapping[m]['hwcid']
                msg = {}
                if re.match(r"display\d+$", m):
                    try:
                        audio_meter = mapping[m]['audio_meter']
                    except KeyError:
                        pass
                    else:
                        try:
                            data1 = v[mapping[m]['channels'][0]]
                        except (KeyError, IndexError):
                            data1 = None
                        try:
                            data2 = v[mapping[m]['channels'][1]]
                        except (KeyError, IndexError):
                            data2 = None
                        peak1 = data1
                        peak2 = data2
                msg.update(await self._set_audio_meter(hwcid,
                                                       data1=data1,
                                                       data2=data2,
                                                       peak1=peak1,
                                                       peak2=peak2,
                                                       **audio_meter))
                await self.send(msg)

    async def _set_mode(self, hwcid, state=None,
                        blink_pattern=None, output=False):
        msg = {
            "HWCIDs": [hwcid],
            "HWCMode": {}
        }
        if state:
            msg["HWCMode"]["State"] = state
        if blink_pattern:
            msg["HWCMode"]["BlinkPattern"] = blink_pattern
        if output:
            msg["HWCMode"]["Output"] = True
        return msg

    async def _move_fader(self, hwcid, value):
        msg = {
            "HWCIDs": [hwcid],
            "HWCExtended": {
                "Interpretation": 5
            }
        }
        if value:
            msg["HWCExtended"]["Value"] = value
        return msg

    async def _set_color(self, hwcid, index=None, rgb=None):
        if rgb:
            color = {
                "ColorRGB": rgb
            }
        elif index:
            color = {
                "ColorIndex": {
                    "Index": index
                }
            }
        else:
            color = {
                "ColorIndex": {}
            }
        msg = {
            "HWCIDs": [hwcid],
            "HWCColor": color
        }
        return msg

    async def _set_text(self, hwcid, value1=None, title=None,
                        solid_header=False, text1=None,
                        formatting=7):
        msg = {
            "HWCIDs": [hwcid],
            "HWCText": {}
        }
        if value1:
            msg["HWCText"]["IntegerValue"] = value1
        if title:
            msg["HWCText"]["Title"] = title
        if solid_header:
            msg["HWCText"]["SolidHeaderBar"] = True
        if text1 is not None:
            msg["HWCText"]["TextLine1"] = str(text1)
        if formatting:
            msg["HWCText"]["Formatting"] = formatting
        return msg

    async def _set_audio_meter(self, hwcid, meter_type=1, mono=0,
                               title=None, w=176, h=32,
                               data1=None, peak1=None,
                               data2=None, peak2=None):
        msg = {
            "HWCIDs": [hwcid],
            "Processors": {
                "Audiometer": {
                    "MeterType": meter_type,
                    "W": w,
                    "H": h
                }
            }
        }
        if mono:
            msg["Processors"]["Audiometer"]["Mono"] = mono
        if title:
            msg["Processors"]["Audiometer"]["Title"] = title
        if data1:
            msg["Processors"]["Audiometer"]["Data1"] = data1
        if peak1:
            msg["Processors"]["Audiometer"]["Peak1"] = peak1
        if data2:
            msg["Processors"]["Audiometer"]["Data2"] = data2
        if peak2:
            msg["Processors"]["Audiometer"]["Peak2"] = peak2
        return msg
