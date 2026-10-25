from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket
import time
from typing import Any

from binding import SOURCE_RAW_PANEL

logger = logging.getLogger(__name__)

def normalize_hwc_value(value: str) -> str:
    """Panel protocol: strip optional prefix ('State:Down' → 'Down')."""
    try:
        _prefix, v = value.split(':', 1)
        return v
    except ValueError:
        return value


class RawPanel:
    def __init__(
        self,
        host: str,
        port: int = 9923,
        mode: str = 'ASCII',
        delay: float = 0.01,
        idle_sleep: float = 0.1,
        sleep_timeout_minutes: int = 0,
        sleep_mode: int = 0,
        sleep_screen_saver: int = 0,
        panel_brightness_leds: int = 4,
        panel_brightness_oleds: int = 2,
        panel_dimmed_gain: int = 20,
    ) -> None:
        self.mode = mode
        self.host = str(host)
        self.port = int(port)
        self.connected = False
        self._connection_in_progress = False
        self._disconnect_in_progress = False
        self._stopped = False
        self._reader = None
        self._writer = None
        self._sys_stat = None
        self.delay = delay
        self.idle_sleep = idle_sleep
        self.sleep_timeout_ms = sleep_timeout_minutes * 60000
        self.sleep_mode = sleep_mode
        self.sleep_screen_saver = sleep_screen_saver
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
            "panel_sleep_timeout": None,
            "EnvironmentalHealth": None,
            "DimmedGain": None,
        }
        self._panel_map = {}
        self._panel_topology = {"svgbase": None, "HWC": None}
        self.panel_brightness_leds = panel_brightness_leds
        self.panel_brightness_oleds = panel_brightness_oleds
        self.panel_dimmed_gain = panel_dimmed_gain
        self._commands = {
            "SysStat": self._update_sys_stat,
            "_model": self._update_model,
            "_serial": self._update_serial,
            "_version": self._update_version,
            "_name": self._update_name,
            "_platform": self._update_platform,
            "_bluePillReady": self._update_blue_pill_ready,
            "_panelType": self._update_panel_type,
            "_support": self._update_support,
            "_isSleeping": self._update_is_sleeping,
            "_sleepTimer": self._update_panel_sleep_timeout,
            "_panelTopology_svgbase": self._update_panel_topology_svgbase,
            "_panelTopology_HWC": self._update_panel_topology_hwc,
            "EnvironmentalHealth": self._update_environmental_health,
            "DimmedGain": self._update_dimmed_gain,
            "map": self._update_map,
            "HWC": self._hardware_change_schedule
        }
        self._hw_change_buffer = {}
        self.last_activity = 0.0
        self._last_wakeup = 0.0
        self._wakeup_min_interval = 10.0  # seconds
        self._change_controller = None
        self._feedback_controller = None

    def _update_sys_stat(self, value: str) -> None:
        self._sys_stat = value
        logger.debug("Updated System Stats")

    def _update_model(self, value: str) -> None:
        self.info['model'] = value

    def _update_serial(self, value: str) -> None:
        self.info['serial'] = value

    def _update_version(self, value: str) -> None:
        self.info['version'] = value

    def _update_name(self, value: str) -> None:
        self.info['name'] = value

    def _update_platform(self, value: str) -> None:
        self.info['platform'] = value

    def _update_blue_pill_ready(self, value: str) -> None:
        self.info['bluePillReady'] = value

    def _update_panel_type(self, value: str) -> None:
        self.info['panelType'] = value

    def _update_support(self, value: str) -> None:
        self.info['support'] = value

    async def _update_is_sleeping(self, value: str) -> None:
        prev_state = self.info['isSleeping']
        new_state = bool(int(value))
        if new_state == prev_state:
            return
        if prev_state is not None:
            sleep_state_str = f"{prev_state} -> {new_state}"
        else:
            sleep_state_str = f"{new_state}"
        logger.info("Sleeping: %s", sleep_state_str)
        self.info['isSleeping'] = new_state
        if not new_state and (prev_state or prev_state is None):
            # Init feedback only after panel wakes up or initializes
            await asyncio.sleep(1.0)
            if self._feedback_controller is not None:
                await self._feedback_controller.init_feedback()

    def _update_panel_sleep_timeout(self, value: str) -> None:
        prev_state = self.info['panel_sleep_timeout']
        new_state = int(value)
        if new_state == prev_state:
            return
        if prev_state is not None:
            sleep_timer_str = f"{prev_state} -> {new_state}"
        else:
            sleep_timer_str = f"{new_state}"
        logger.info("Internal Panel Sleep Timer: %s", sleep_timer_str)
        self.info['panel_sleep_timeout'] = new_state

    def _update_environmental_health(self, value: str) -> None:
        self.info['EnvironmentalHealth'] = value

    def _update_dimmed_gain(self, value: str) -> None:
        self.info["DimmedGain"] = value

    def _update_map(self, value: str) -> None:
        k, v = value.split(":")
        self._panel_map[k] = v

    def _update_panel_topology_svgbase(self, value: str) -> None:
        self._panel_topology["svgbase"] = value

    def _update_panel_topology_hwc(self, value: str) -> None:
        self._panel_topology["HWC"] = json.loads(value)

    def _hardware_change_schedule(self, hwcid: str, value: str) -> None:
        t = time.perf_counter()
        self.last_activity = t
        change = self._hw_change_buffer.setdefault(hwcid, {'time': t,
                                                          'value': None})
        change['value'] = value

    async def _hardware_change_process(self, hwcid: str, value: str) -> None:
        await self.wakeup()
        if self._change_controller is None:
            logger.debug("No change controller; ignore hwcid %s", hwcid)
            return
        normalized = normalize_hwc_value(value)
        await self._change_controller.on_change(
            {"hwcid": str(hwcid), "value": normalized},
            source=SOURCE_RAW_PANEL,
        )


    def _configure_socket(self, writer: asyncio.StreamWriter) -> None:
        """Keepalive for dead-peer detection;
           TCP_NODELAY for low-latency meters."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # Linux; ignore if unsupported (e.g. some non-Linux builds)
            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            if hasattr(socket, "TCP_KEEPINTVL"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            if hasattr(socket, "TCP_KEEPCNT"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            if hasattr(socket, "TCP_USER_TIMEOUT"):
                sock.setsockopt(
                    socket.IPPROTO_TCP,
                    socket.TCP_USER_TIMEOUT,
                    10_000,
                )
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            logger.debug("Could not configure panel socket options: %s", e)

    def stop(self) -> None:
        """Signal long-running loops to exit; blocks reconnect."""
        self._stopped = True

    def set_change_controller(self, controller: Any) -> None:
        """Attach controller implementing on_change(data, *, source)."""
        self._change_controller = controller

    def set_feedback_controller(self, controller: Any) -> None:
        """Attach controller implementing init_feedback()."""
        self._feedback_controller = controller

    async def connect(
        self,
        retries: int | None = 20,
        retry_interval_sec: int | float = 10,
        timeout: int | float = 10,
    ) -> None:
        if self._stopped:
            logger.info("Shutdown is in progress, connect aborted")
            return
        if self._disconnect_in_progress:
            logger.info("Disconnect is in progress, connect aborted")
            return
        if self._connection_in_progress:
            logger.info("Another connection is in progress, connect aborted")
            return
        self._connection_in_progress = True
        logger.info("Connecting to %s:%s...", self.host, self.port)
        attempt = 0
        try:
            while not self.connected and not self._stopped:
                attempt += 1
                try:
                    self._reader, self._writer = await asyncio.wait_for(
                        asyncio.open_connection(
                            self.host,
                            self.port,
                        ),
                        timeout=timeout
                    )
                except (ConnectionRefusedError, OSError, TimeoutError) as e:
                    logger.warning(
                        "Connection to %s:%s failed (%s)",
                        self.host,
                        self.port,
                        type(e).__name__,
                    )
                    logger.debug("%r", e)
                    if retries is not None and attempt > retries:
                        logger.error(
                            "Maximum retries reached for %s:%s",
                            self.host,
                            self.port,
                        )
                        break
                    await asyncio.sleep(retry_interval_sec)
                else:
                    self._configure_socket(self._writer)
                    self.connected = True
                    logger.info(
                        "Connected to %s:%s after %s attempts.",
                        self.host,
                        self.port,
                        attempt,
                    )
        finally:
            self._connection_in_progress = False
        if self.connected and not self._stopped:
            await self.initialize()

    async def initialize(self) -> None:
        # Responses from the panel are handled only after the panel
        # handlers are started
        # TODO: Try await self.receive() for each expected response,
        # may not work with handle_requests() loop.
        await self.send(self._get_panel_info())
        await self.send(self._get_panel_map())
        await self.send(self._get_panel_topology())
        await self.send(self._get_sleep_timeout())
        await self.send(self._set_sleep_timeout(self.sleep_timeout_ms))
        await self.send(self._set_sleep_mode(self.sleep_mode))
        await self.send(self._set_sleep_screen_saver(self.sleep_screen_saver))
        await self.send(self._set_panel_brightness(
            self.panel_brightness_leds,
            self.panel_brightness_oleds,
        ))
        await self.send(self._set_dimmed_gain(self.panel_dimmed_gain))
        # Yield for other background tasks
        await asyncio.sleep(0)
        logger.info("Raw Panel %s is initialized", self.host)

    async def disconnect(self, purge: bool = False) -> None:
        if self._disconnect_in_progress:
            logger.info(
                "Another disconnect is in progress, disconnect aborted"
            )
            return
        if self._connection_in_progress:
            logger.info("Connect is in progress, disconnect aborted")
            return

        self._disconnect_in_progress = True
        logger.info("Closing connection to %s:%s...", self.host, self.port)

        writer = self._writer
        self._reader = None
        self._writer = None
        self.connected = False

        try:
            if writer is not None:
                try:
                    writer.close()
                    await asyncio.wait_for(writer.wait_closed(), timeout=2)
                except (TimeoutError, ConnectionResetError, OSError) as e:
                    logger.warning(
                        "Can't close the connection gracefully (%s); "
                        "aborting transport",
                        type(e).__name__,
                    )
                    logger.debug("%r", e)
                    transport = writer.transport
                    if transport is not None and not transport.is_closing():
                        transport.abort()
            logger.info("Connection to %s:%s is closed", self.host, self.port)
            if purge:
                self._purge_panel_info()
        finally:
            self._disconnect_in_progress = False

    def _purge_panel_info(self) -> None:
        for key in self.info:
            self.info[key] = None
        logger.info("Panel info was purged")

    async def handle_lost_connection(self) -> None:
        logger.warning("Connection to %s:%s was lost", self.host, self.port)
        if self._stopped:
            logger.info(
                "Shutdown is in progress, not handling lost connection"
            )
            return
        if self._disconnect_in_progress:
            logger.info(
                "Disconnect is in progress, not handling lost connection"
            )
            return
        if self._connection_in_progress:
            logger.info(
                "Connect is in progress, not handling lost connection"
            )
            return
        await self.disconnect(purge=True)

    async def wakeup(self, force: bool = False) -> None:
        """Send wakeup message.

        Throttled by default so high-rate feedback does not spam the panel.
        """
        now = time.perf_counter()
        if force or (now - self._last_wakeup) > self._wakeup_min_interval:
            await self.send(self._wakeup())
            self._last_wakeup = now

    async def handle_request(self, request: str) -> None:
        try:
            key, value = request.split('=', 1)
        except ValueError:
            if not len(request):
                logger.debug("Request is empty line")
                return
            elif request == 'nack':
                logger.warning("Request is 'nack'")
                return
            else:
                logger.warning("Invalid request: %s", request)
                return
        try:
            command, hwcid = key.split('#')
        except ValueError:
            command = key
            params = (value,)
        else:
            params = hwcid, value
        try:
            cmd = self._commands[command]
        except KeyError:
            logger.warning("Unhandled panel message: %s", request)
            return
        # Handle both async and sync methods
        if inspect.iscoroutinefunction(cmd):
            await cmd(*params)
        else:
            cmd(*params)

    async def process_buffers(self) -> None:
        logger.info("Processing buffered hardware changes...")
        while not self._stopped:
            try:
                if not self._hw_change_buffer:
                    await asyncio.sleep(self.idle_sleep)
                    continue
                for hwid, v in self._hw_change_buffer.items():
                    t = time.perf_counter()
                    value = v['value']
                    if t - v['time'] >= self.delay:
                        del self._hw_change_buffer[hwid]
                        await self._hardware_change_process(hwid, value)
                    break
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                break
        logger.info("Buffer processing finished")

    async def handle_requests(self) -> None:
        logger.info("Handling requests from the panel...")
        while not self._stopped:
            try:
                r = await self.receive()
                if r is None:
                    await asyncio.sleep(0.1)
                    continue
                await self.handle_request(r)
            except asyncio.CancelledError:
                break
        logger.info("Requests from the panel are not handled anymore")

    @staticmethod
    def batch_hwc(messages: list[dict]) -> dict | list | None:
        """Collapse panel messages into one write.

        Messages with the same body share one HWCIDs list. Different
        bodies are wrapped as [{"States": [...]}]. One unique body is
        sent as-is. Insertion order is kept.
        """
        if not messages:
            return None
        grouped: dict[str, dict] = {}
        for msg in messages:
            body = {k: v for k, v in msg.items() if k != "HWCIDs"}
            key = json.dumps(body, separators=(",", ":"), sort_keys=True)
            existing = grouped.setdefault(key, msg)
            if existing is not msg:
                existing["HWCIDs"].extend(msg["HWCIDs"])
        states = list(grouped.values())
        if len(states) == 1:
            return states[0]
        return [{"States": states}]

    async def send(
        self, message: dict | list, timeout: int | float = 1
    ) -> None:
        if self._stopped:
            return
        if not self.connected:
            await self.connect(retries=None)  # Infinite reconnect
        while (
            not self._stopped
            and (self._connection_in_progress or self._disconnect_in_progress)
        ):
            await asyncio.sleep(0.1)
        if self._stopped or not self.connected or self._writer is None:
            return

        payload = json.dumps(message, separators=(',', ':'))
        logger.debug(payload)
        try:
            self._writer.write(f"{payload}\n".encode('ascii'))
            await asyncio.wait_for(self._writer.drain(), timeout=timeout)
            transport = self._writer.transport
            logger.debug(
                "write_buf=%s closing=%s",
                transport.get_write_buffer_size(),
                self._writer.is_closing(),
            )
        except (ConnectionResetError, OSError, TimeoutError) as e:
            logger.warning(
                "Message was not delivered: %s (%s)",
                payload,
                type(e).__name__,
            )
            logger.debug("%r", e)
            await self.handle_lost_connection()

    async def receive(self) -> str | None:
        if self._stopped:
            return None
        if not self.connected:
            await self.connect(retries=None)  # Infinite reconnect
        while (
            not self._stopped
            and (self._connection_in_progress or self._disconnect_in_progress)
        ):
            await asyncio.sleep(0.1)
        if self._stopped or not self.connected or self._reader is None:
            return None
        try:
            raw_record = await self._reader.readline()
        except (ConnectionResetError, OSError, TimeoutError) as e:
            logger.warning(
                "Panel read failed/timed out (%s)",
                type(e).__name__,
            )
            logger.debug("%r", e)
            await self.handle_lost_connection()
            return None

        # Clean peer close or half-close
        if not raw_record:
            logger.warning("Panel connection closed (EOF)")
            await self.handle_lost_connection()
            return None

        try:
            record = raw_record.decode().strip()
        except UnicodeDecodeError:
            logger.warning("Undecodable panel data: %r", raw_record)
            return None
        logger.debug(record)
        return record

    # TODO: Refactor message building logic
    def _get_panel_info(self) -> list:
        return [{"Command": {"SendPanelInfo": True}}]

    def _get_panel_map(self) -> list:
        return [{"Command": {"ReportHWCavailability": True}}]

    def _get_panel_topology(self) -> list:
        return [{"Command": {"SendPanelTopology": True}}]

    def _wakeup(self) -> list:
        return [{"Command": {"WakeUp": True}}]

    def _get_sleep_timeout(self) -> list:
        return [{"Command": {"GetSleepTimeout": True}}]

    def _set_sleep_timeout(self, timeout_ms: int) -> list:
        return [{"Command": {"SetSleepTimeout": {"Value": timeout_ms}}}]

    def _set_sleep_mode(self, value: int = 0) -> list:
        """
        0 = FireWorks (Default) - LEDs will animate on the panel
        1 = Buttons Off
        """
        return [{"Command": {"SetSleepMode": {"Value": value}}}]

    def _set_sleep_screen_saver(self, value: int = 0) -> list:
        """
        0 = "Wake Up On Key Press" message (default)
        1 = "Sheep And Goats" - Classic UniSketch funtime screen saver
        2 = "Save The Oleds" message
        3 = Just Dimmed - keeps content, just dims the panel
        """
        return [{"Command": {"SetSleepScreenSaver": {"Value": value}}}]

    def _set_dimmed_gain(self, value: int = 0) -> list:
        """
        Sets the gain level (0, 1-64) for the dimmed button state on the panel.
        Full gain is 64, a good dimmed value is around 4-16.
        Setting DimmedGain to zero will reset it to the panel defaults (10-16).
        """
        return [{"Command": {"SetDimmedGain": {"Value": value}}}]

    def _set_panel_brightness(self, x: int = 8, y: int | None = None) -> list:
        """
        Brightness for LEDs (x) and OLEDs (y). Value range 0-8.
        """
        if y is None:
            y = x
        return [{"Command": {"PanelBrightness": {"LEDs": x, "OLEDs": y}}}]

    def _set_mode(
        self,
        hwcid: int,
        state: int | None = None,
        blink_pattern: int | None = None,
        output: bool = False,
    ) -> dict:
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

    def _move_fader(self, hwcid: int, value: int | None) -> dict:
        msg = {
            "HWCIDs": [hwcid],
            "HWCExtended": {
                "Interpretation": 5
            }
        }
        if value:
            msg["HWCExtended"]["Value"] = value
        return msg

    def _set_color(
        self, hwcid: int, index: int | None = None, rgb: dict | None = None
    ) -> dict:
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

    def _set_text(
        self,
        hwcid: int,
        value1: int | None = None,
        title: str | None = None,
        solid_header: bool = False,
        text1: str | None = None,
        formatting: int = 7,
    ) -> dict:
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

    def _set_audio_meter(
        self,
        hwcid: int,
        meter_type: int = 1,
        mono: int = 0,
        title: str | None = None,
        w: int = 176,
        h: int = 32,
        data1: int | None = None,
        peak1: int | None = None,
        data2: int | None = None,
        peak2: int | None = None,
    ) -> dict:
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
