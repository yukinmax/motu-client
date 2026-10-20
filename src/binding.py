"""Motu ↔ panel change controllers and maps.

Protocol shared by RawPanel and Store:

  set_change_controller(controller)
  controller.on_change(data, *, source: str)

Sources:
  "raw_panel"  — HWC event: data = {"hwcid": str, "value": str}
  "datastore"  — Motu path diff (Store.base_path)
  "meters"     — Motu meters diff (Store.base_path)
"""

from __future__ import annotations

import logging
import os
import time
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Tuple

import motu

logger = logging.getLogger(__name__)

SOURCE_RAW_PANEL = "raw_panel"
SOURCE_DATASTORE = "datastore"
SOURCE_METERS = "meters"

OVERRIDE_PATH = "mix/monitor/0/override"
OVERRIDE_BASE = 1024.0
OVERRIDE_DISABLED = -1.0

_PATH_ROLE_RE = re.compile(r"(\w+)/?(\d*)$")


def role_from_path(path: str) -> str:
    """Infer control role from the last Motu path segment."""
    m = _PATH_ROLE_RE.search(path)
    if not m:
        return "unknown"
    name = m.group(1)
    # TODO: Remove the checks when dispatcher table is inplace
    if name in ("send", "fader"):
        return "send"
    if name in ("mute", "solo", "override"):
        return name
    return name


@dataclass(frozen=True, slots=True)
class BindingConfig:
    """Resolved HWC → Motu binding. Optional fields are role-specific."""

    path: str
    role: str
    default_level_db: float | None = None
    override_index: int | None = None

    @property
    def override_value(self) -> float | None:
        if self.override_index is None:
            return None
        return OVERRIDE_BASE + float(self.override_index)


class ControlMap:
    """hwcid → BindingConfig (roles resolved once at load)."""

    def __init__(self, bindings: Mapping[str, BindingConfig]):
        self._bindings = dict(bindings)

    @classmethod
    def from_raw(cls, raw: Mapping[str, Mapping[str, Any]]) -> "ControlMap":
        out: dict[str, BindingConfig] = {}
        for hwcid, entry in raw.items():
            path = entry["path"]
            role = entry.get("role") or role_from_path(path)
            default = entry.get("default_level")
            override_index = entry.get("override_index")
            out[str(hwcid)] = BindingConfig(
                path=path,
                role=role,
                default_level_db=(
                    float(default) if default is not None else None
                ),
                override_index=(
                    int(override_index) if override_index is not None else None
                ),
            )
        return cls(out)

    def resolve(self, hwcid: str) -> BindingConfig | None:
        return self._bindings.get(hwcid)

    def __contains__(self, hwcid: str) -> bool:
        return hwcid in self._bindings

    def __len__(self) -> int:
        return len(self._bindings)

    def items(self) -> Iterable[Tuple[str, BindingConfig]]:
        return self._bindings.items()

    def keys(self):
        return self._bindings.keys()


class FeedbackMap:
    """Motu path → panel feedback descriptors."""

    def __init__(self, bindings: Mapping[str, Mapping[str, Any]]):
        # Shallow copy: long-lived map must not track later table mutations.
        self._bindings = dict(bindings)

    @classmethod
    def from_raw(cls, raw: Mapping[str, Mapping[str, Any]]) -> "FeedbackMap":
        return cls(raw)

    def resolve(self, path: str) -> Mapping[str, Any] | None:
        return self._bindings.get(path)

    def __contains__(self, path: str) -> bool:
        return path in self._bindings

    def __len__(self) -> int:
        return len(self._bindings)

    def items(self) -> Iterable[Tuple[str, Mapping[str, Any]]]:
        return self._bindings.items()

    def keys(self):
        return self._bindings.keys()


class MotuController:
    """Panel HWC → Motu datastore."""

    def __init__(self, control_map: ControlMap, *, range_mapping: tuple):
        self._map = control_map
        self._range_mapping = range_mapping
        self._ds = None

    def set_datastore(self, datastore) -> None:
        self._ds = datastore

    async def on_change(self, data: dict, *, source: str) -> None:
        if self._ds is None:
            logger.debug(
                "MotuController: no datastore; ignore source=%s", source
            )
            return
        if source != SOURCE_RAW_PANEL:
            logger.debug("MotuController: ignore source=%s", source)
            return
        await self.process_hwc(data["hwcid"], data["value"])

    async def process_hwc(self, hwcid: str | int, value: str) -> None:
        # TODO: Prepare to handle 'Up' values as well.
        """Apply one normalized HWC value ('Down' / 'Up' / fader level)."""
        cfg = self._map.resolve(hwcid)
        if cfg is None:
            logger.debug("hwcid %s is not mapped", hwcid)
            return
        logger.debug("hwcid %s value=%r role=%s", hwcid, value, cfg.role)

        # TODO: Consider replacing with dispatch table
        if cfg.role == "send":
            await self._handle_send(cfg, value)
        elif cfg.role == "mute":
            await self._handle_mute(cfg, value)
        elif cfg.role == "solo":
            await self._handle_solo(cfg, value)
        elif cfg.role == "override":
            await self._handle_override(cfg, value)
        else:
            logger.debug("Unhandled role %s for hwcid %s", cfg.role, hwcid)

    def _level_from_panel_value(
        self,
        cfg: BindingConfig,
        v: str
    ) -> float | None:
        try:
            raw = int(v)
        except ValueError:
            if v != "Down":
                return None
            current = self._ds.get(cfg.path)
            if cfg.default_level_db is None:
                logger.debug("No default level for path %s", cfg.path)
                return 1.0
            default = motu.level_from_db(cfg.default_level_db)
            return default if current != default else 1.0
        db = motu.db_from_raw(raw, self._range_mapping)
        return motu.level_from_db(db)

    async def _handle_send(self, cfg: BindingConfig, v: str) -> None:
        level = self._level_from_panel_value(cfg, v)
        if level is None:
            return
        await self._ds.set(cfg.path, level)

    async def _handle_mute(self, cfg: BindingConfig, v: str) -> None:
        if v != "Down":
            return
        await self._ds.toggle(cfg.path)

    async def _handle_solo(self, cfg: BindingConfig, v: str) -> None:
        if v != "Down":
            return
        override = self._ds.get(OVERRIDE_PATH)
        if override is not None and override != OVERRIDE_DISABLED:
            await self._ds.set(cfg.path, 1.0)
        else:
            await self._ds.toggle(cfg.path)
        await self._ds.set(OVERRIDE_PATH, OVERRIDE_DISABLED)

    async def _handle_override(self, cfg: BindingConfig, v: str) -> None:
        if v != "Down":
            return
        if cfg.override_value is None:
            logger.warning(
                "Missing override_index for %s", cfg.path
            )
            return
        current = self._ds.get(cfg.path)
        if current == cfg.override_value:
            await self._ds.set(cfg.path, OVERRIDE_DISABLED)
        else:
            await self._ds.set(cfg.path, cfg.override_value)


class PanelController:
    """Motu → panel feedback.

    Public: on_change, init_feedback, set_panel, set_datastore, set_meters
    Private: _process_data_feedback, _process_meters_feedback
    """

    def __init__(
        self,
        feedback_map: FeedbackMap,
        *,
        faders_range_mapping: tuple,
        meters_range_mapping: tuple,
    ):
        self._map = feedback_map
        self._faders_range_mapping = faders_range_mapping
        self._meters_range_mapping = meters_range_mapping
        self._panel = None
        self._ds = None
        self._ms = None

    def set_panel(self, panel) -> None:
        self._panel = panel

    def set_datastore(self, datastore) -> None:
        self._ds = datastore

    def set_meters(self, meters) -> None:
        self._ms = meters

    async def on_change(self, data: dict, *, source: str) -> None:
        if self._panel is None:
            logger.debug("PanelController: no panel; ignore source=%s", source)
            return
        if source == SOURCE_DATASTORE:
            await self._process_data_feedback(data)
        elif source == SOURCE_METERS:
            await self._process_meters_feedback(data)
        else:
            logger.debug("PanelController: ignore source=%s", source)

    async def init_feedback(self) -> None:
        """Seed panel from current Motu snapshots."""
        if self._panel is None:
            logger.warning("PanelController.init_feedback: no panel")
            return
        if not self._ds:
            logger.warning("PanelController.init_feedback: no datastore")
            return
        if not self._ms:
            logger.warning("PanelController.init_feedback: no meters")
            return
        logger.info("Initializing the panel feedback")
        dd = {}
        md = {}
        for path in self._map.keys():
            try:
                v = self._ds.get(path)
            except KeyError:
                for i in [str(s) for s in range(15)] + ["peaks"]:
                    try:
                        v = self._ms.get(f"{path}/{i}")
                    except KeyError:
                        logger.warning("Path %s is not available", path)
                        continue
                    else:
                        md[path] = v
            else:
                dd[path] = v
        logger.debug("Init datastore feedback %s", dd)
        await self._process_data_feedback(dd)
        logger.debug("Init meters feedback %s", md)
        await self._process_meters_feedback(md)

    async def _process_data_feedback(self, d):
        # TODO: Split and refactor
        # TODO: Consider waking the panel
        # TODO: Batch the events into 1 message
        for k, v in d.items():
            mapping = self._map.resolve(k)
            if mapping is None:
                logger.debug("path %s is not mapped", k)
                continue
            t = re.search(r"\w+$", k)[0]
            if t in ('send', 'fader'):
                db_value = motu.level_to_db(float(v))
                raw_value = motu.db_from_raw(
                    db_value,
                    self._faders_range_mapping,
                    reverse=True
                )
            for m in mapping:
                hwcid = mapping[m]['hwcid']
                msg = {}
                if m.startswith(('button', 'fader')):
                    try:
                        k = v == mapping[m]['value']
                    except KeyError:
                        k = v
                    try:
                        mode = mapping[m]['mode'].copy()
                    except KeyError:
                        pass
                    else:
                        for mode_type, mode_value in mode.items():
                            try:
                                mode[mode_type] = (
                                    mode_value[0] if k else mode_value[1]
                                )
                            except TypeError:
                                pass
                        msg.update(self._panel._set_mode(hwcid, **mode))
                    try:
                        color = mapping[m]['color'].copy()
                    except KeyError:
                        pass
                    else:
                        for color_type, color_value in color.items():
                            try:
                                color[color_type] = (
                                    color_value[0] if k else color_value[1]
                                )
                            except TypeError:
                                pass
                        msg.update(self._panel._set_color(hwcid, **color))
                if m == 'fader':
                    msg.update(self._panel._move_fader(hwcid, raw_value))
                elif m == 'display':
                    try:
                        txt = mapping[m]['text']
                    except KeyError:
                        txt = {}
                    msg.update(self._panel._set_text(
                        hwcid,
                        text1=db_value,
                        **txt,
                    ))
                await self._panel.send(msg)

    async def _process_meters_feedback(self, d):
        # TODO: Split and refactor
        # Currently sends data for all meters even if only 1 meter data changed
        self._panel.last_activity = time.perf_counter()
        await self._panel.wakeup()
        msg = {}
        base_path = 'mix/level'
        mapping = self._map.resolve(base_path)
        if mapping is None:
            logger.warning("path %s is not mapped", base_path)
            return
        for m in mapping:
            hwcid = mapping[m]['hwcid']
            data1 = None
            data2 = None
            peak1 = None
            peak2 = None
            multiplier = 1
            if re.match(r"display\d+$", m):
                try:
                    pre = mapping[m]['pre_fader']
                except KeyError:
                    pass
                else:
                    if not pre:
                        try:
                            multiplier = self._ds.get(
                                mapping[m]['fader_path']
                            ) * int(not self._ds.get(
                                mapping[m]['mute_path'])
                            )
                        except KeyError:
                            logger.warning(
                                "fader_path and mute_path should be \
                                configured if pre_fader is set to False"
                            )
                try:
                    audio_meter = mapping[m]['audio_meter']
                except KeyError:
                    pass
                else:
                    for k, v in d.items():
                        if not k.startswith(base_path):
                            logger.debug("Skipping feedback: %s: %s", k, v)
                            continue
                        try:
                            k, s = os.path.split(k)
                        except Exception:
                            logger.warning("Can't split %s", k)
                        if s != 'peaks':
                            try:
                                data1 = v[mapping[m]['channels'][0]]
                            except (KeyError, IndexError):
                                pass
                            else:
                                data1 = self._level_to_raw(
                                    data1,
                                    self._meters_range_mapping,
                                    multiplier=multiplier
                                )
                            try:
                                data2 = v[mapping[m]['channels'][1]]
                            except (KeyError, IndexError):
                                pass
                            else:
                                data2 = self._level_to_raw(
                                    data2,
                                    self._meters_range_mapping,
                                    multiplier=multiplier
                                )
                        else:
                            try:
                                peak1 = v[mapping[m]['channels'][0]]
                            except (KeyError, IndexError):
                                pass
                            else:
                                peak1 = self._level_to_raw(
                                    peak1,
                                    self._meters_range_mapping,
                                    multiplier=multiplier
                                )
                            try:
                                peak2 = v[mapping[m]['channels'][1]]
                            except (KeyError, IndexError):
                                pass
                            else:
                                peak2 = self._level_to_raw(
                                    peak2,
                                    self._meters_range_mapping,
                                    multiplier=multiplier
                                )
                    msg.update(self._panel._set_audio_meter(
                        hwcid,
                        data1=data1,
                        data2=data2,
                        peak1=peak1,
                        peak2=peak2,
                        **audio_meter,
                    ))
                await self._panel.send(msg)

    def _level_to_raw(self, value, range_mapping, multiplier=1):
        db = motu.level_to_db(float(value * multiplier / 1000))
        return motu.db_from_raw(db, range_mapping, reverse=True)
