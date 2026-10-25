"""Motu ↔ panel change controllers and maps.

Protocol shared by RawPanel and _Store:

  set_change_controller(controller)
  controller.on_change(data, *, source: str)

Sources:
  "raw_panel"  — HWC event: data = {"hwcid": str, "value": str}
  "datastore"  — Motu path diff (_Store.base_path)
  "meters"     — Motu meters diff (_Store.base_path)
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import raw_panel

import motu

logger = logging.getLogger(__name__)

SOURCE_RAW_PANEL = "raw_panel"
SOURCE_DATASTORE = "datastore"
SOURCE_METERS = "meters"

OVERRIDE_PATH = "mix/monitor/0/override"
OVERRIDE_BASE = 1024.0
OVERRIDE_DISABLED = -1.0

# Panel raw ↔ Motu dB.
FADERS_RANGE_MAPPING = (
    (0, -math.inf),
    ((1, 5), (-120, -60)),
    ((5, 125), (-60, -30)),
    ((125, 1000), (-30, 12)),
)
METERS_RANGE_MAPPING = (
    (0, -math.inf),
    ((1, 1000), (-60, 12)),
)

_PATH_ROLE_RE = re.compile(r"(\w+)/?(\d*)$")


def _segments(mapping: tuple, *, reverse: bool = False) -> tuple:
    """Point (src, dst) or range ((lo, hi), (dst_lo, dst_hi)) → segments."""
    out = []
    for src, dst in mapping:
        if not isinstance(src, tuple):
            src, dst = (src, src), (dst, dst)
        if reverse:
            src, dst = dst, src
        out.append((float(src[0]), float(src[1]), float(dst[0]), float(dst[1])))
    return tuple(out)


_FADERS_RAW_TO_DB = _segments(FADERS_RANGE_MAPPING)
_FADERS_DB_TO_RAW = _segments(FADERS_RANGE_MAPPING, reverse=True)
_METERS_RAW_TO_DB = _segments(METERS_RANGE_MAPPING)
_METERS_DB_TO_RAW = _segments(METERS_RANGE_MAPPING, reverse=True)


def _lerp(value: float, segments: tuple) -> float:
    first_lo, _, first_dst, _ = segments[0]
    _, last_hi, _, last_dst = segments[-1]
    if value <= first_lo:
        return first_dst
    if value >= last_hi:
        return last_dst
    for lo, hi, dst_lo, dst_hi in segments:
        if hi == lo:
            continue
        if lo <= value < hi:
            return dst_lo + (value - lo) / (hi - lo) * (dst_hi - dst_lo)
    return first_dst


def raw_to_db(value: int | float, range_mapping: tuple) -> float:
    """Panel raw → dB. Accepts int or float."""
    segments = (
        _FADERS_RAW_TO_DB
        if range_mapping is FADERS_RANGE_MAPPING
        else _METERS_RAW_TO_DB
    )
    return _lerp(float(value), segments)


def db_to_raw(value: int | float, range_mapping: tuple) -> int:
    """dB → panel raw. Accepts int or float."""
    segments = (
        _FADERS_DB_TO_RAW
        if range_mapping is FADERS_RANGE_MAPPING
        else _METERS_DB_TO_RAW
    )
    return int(_lerp(float(value), segments))


def role_from_path(path: str) -> str:
    """Infer control role from the last Motu path segment."""
    m = _PATH_ROLE_RE.search(path)
    if not m:
        return "unknown"
    name = m.group(1)
    if name in ("send", "fader"):
        return "send"
    if name in ("mute", "solo", "override"):
        return name
    return name


def kind_from_name(name: str) -> str:
    """Infer feedback kind from the raw target key."""

    if name.startswith("button"):
        return "button"
    if name == "fader":
        return "fader"
    if name == "display":
        return "display"
    if name.startswith("display") and name[7:].isdigit():
        return "meter"
    return "unknown"


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


@dataclass(frozen=True, slots=True)
class FeedbackTarget:
    """Normalized feedback target. Built once from the raw table."""

    # TODO: Infer kind from the panel topology
    kind: str
    hwcid: int
    mode: dict | None = None
    color: dict | None = None
    value: float | None = None
    text: dict | None = None
    channels: tuple[int, ...] | None = None
    pre_fader: bool = True
    # TODO: Infer fader_path and mute_path from channels
    fader_path: str | None = None
    mute_path: str | None = None
    audio_meter: dict | None = None


class _Map:
    """Shared key → value lookup. Subclasses only implement from_raw."""

    def __init__(self, bindings: Mapping):
        self._bindings = dict(bindings)

    def resolve(self, key: str):
        return self._bindings.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._bindings

    def __len__(self) -> int:
        return len(self._bindings)

    def items(self):
        return self._bindings.items()

    def keys(self):
        return self._bindings.keys()

    def values(self):
        return self._bindings.values()


class ControlMap(_Map):
    """hwcid → BindingConfig (roles resolved once at load)."""

    @classmethod
    def from_raw(cls, raw: Mapping[str, Mapping[str, Any]]) -> ControlMap:
        out: dict[str, BindingConfig] = {}
        for hwcid, entry in raw.items():
            path = entry["path"]
            role = entry.get("role") or role_from_path(path)
            default = entry.get("default_level")
            override_index = entry.get("override_index")
            out[str(hwcid)] = BindingConfig(
                path=path,
                role=role,
                default_level_db=(float(default) if default is not None else None),
                override_index=(
                    int(override_index) if override_index is not None else None
                ),
            )
        return cls(out)


class FeedbackMap(_Map):
    """Motu path → tuple of feedback targets."""

    @classmethod
    def from_raw(cls, raw: Mapping[str, Mapping[str, Any]]) -> FeedbackMap:
        out: dict[str, tuple[FeedbackTarget, ...]] = {}
        for path, targets in raw.items():
            built: list[FeedbackTarget] = []
            for name, cfg in targets.items():
                channels = cfg.get("channels")
                built.append(
                    FeedbackTarget(
                        kind=kind_from_name(name),
                        hwcid=int(cfg["hwcid"]),
                        mode=cfg.get("mode"),
                        color=cfg.get("color"),
                        value=cfg.get("value"),
                        text=cfg.get("text"),
                        channels=tuple(channels) if channels is not None else None,
                        pre_fader=bool(cfg.get("pre_fader", True)),
                        fader_path=cfg.get("fader_path"),
                        mute_path=cfg.get("mute_path"),
                        audio_meter=cfg.get("audio_meter"),
                    )
                )
            out[path] = tuple(built)
        return cls(out)


class MotuController:
    """Panel HWC → Motu datastore."""

    def __init__(self, control_map: ControlMap, *, range_mapping: tuple):
        self._map = control_map
        self._range_mapping = range_mapping
        self._ds = None
        self._role_handlers = {
            "send": self._handle_send,
            "mute": self._handle_mute,
            "solo": self._handle_solo,
            "override": self._handle_override,
        }

    def set_datastore(self, datastore: motu.DataStore) -> None:
        self._ds = datastore

    async def on_change(self, data: dict, *, source: str) -> None:
        if self._ds is None:
            logger.debug("MotuController: no datastore; ignore source=%s", source)
            return
        if source != SOURCE_RAW_PANEL:
            logger.debug("MotuController: ignore source=%s", source)
            return
        await self.process_hwc(data["hwcid"], data["value"])

    async def process_hwc(self, hwcid: str | int, value: str) -> None:
        """Apply one normalized HWC value ('Down' / 'Up' / fader level)."""
        # TODO: Prepare to handle 'Up' values as well.
        cfg = self._map.resolve(hwcid)
        if cfg is None:
            logger.debug("hwcid %s is not mapped", hwcid)
            return
        logger.debug("hwcid %s value=%r role=%s", hwcid, value, cfg.role)

        handler = self._role_handlers.get(cfg.role)
        if handler is None:
            logger.debug("Unhandled role %s for hwcid %s", cfg.role, hwcid)
            return
        await handler(cfg, value)

    def _level_from_panel_value(self, cfg: BindingConfig, v: str) -> float | None:
        try:
            raw = int(v)
        except ValueError:
            if v != "Down":
                return None
            current = self._ds.get(cfg.path)
            if cfg.default_level_db is None:
                logger.debug("No default level for path %s", cfg.path)
                return 1.0
            default = motu.db_to_level(cfg.default_level_db)
            return default if current != default else 1.0
        db = raw_to_db(raw, self._range_mapping)
        return motu.db_to_level(db)

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
            logger.warning("Missing override_index for %s", cfg.path)
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
        self._meter_multipliers: dict[int, float] = {}
        self._silent_meters: set[int] = set()
        self._last_peaks = None
        self._kind_handlers = {
            "button": self._handle_button,
            "fader": self._handle_fader,
            "display": self._handle_display,
        }

    def set_panel(self, panel: raw_panel.RawPanel) -> None:
        self._panel = panel

    def set_datastore(self, datastore: motu.DataStore) -> None:
        self._ds = datastore

    def set_meters(self, meters: motu.Meters) -> None:
        self._ms = meters

    @property
    def _meter_targets(self) -> tuple[FeedbackTarget, ...]:
        return tuple(
            target
            for target in (self._map.resolve("mix/level") or ())
            if target.kind == "meter"
        )

    def _update_meter_multiplier_for_path(self, path: str) -> None:
        for target in self._meter_targets:
            if path not in (target.fader_path, target.mute_path):
                continue
            self._meter_multipliers[target.hwcid] = self._calculate_meter_multiplier(
                target
            )

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
                    m_path = f"{path}/{i}"
                    try:
                        v = self._ms.get(m_path)
                    except KeyError:
                        logger.warning("Path %s is not available", path)
                        continue
                    else:
                        md[m_path] = v
            else:
                dd[path] = v
        logger.debug("Init datastore feedback %s", dd)
        await self._process_data_feedback(dd)
        logger.debug("Init meters feedback %s", md)
        # Force the controller to send data to all audio meters
        self._last_peaks = None
        await self._process_meters_feedback(md)

    @staticmethod
    def _pick_value_by_status(value: Any, active: bool) -> Any:
        """Pair (on, off) or a single scalar."""
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value[0] if active else value[1]
        return value

    def _apply_style(
        self,
        hwcid: int,
        active: bool,
        mode: dict | None = None,
        color: dict | None = None,
    ) -> dict:
        msg: dict = {}
        if mode:
            resolved = {
                key: self._pick_value_by_status(val, active)
                for key, val in mode.items()
            }
            msg.update(self._panel._set_mode(hwcid, **resolved))
        if color:
            resolved = {
                key: self._pick_value_by_status(val, active)
                for key, val in color.items()
            }
            msg.update(self._panel._set_color(hwcid, **resolved))
        return msg

    def _handle_button(
        self,
        target: FeedbackTarget,
        *,
        value: Any,
        raw_value: int | None,
        db_value: float | None,
    ) -> dict:
        if target.value is not None:
            active = value == target.value
        else:
            active = bool(value)
        return self._apply_style(
            target.hwcid,
            active,
            mode=target.mode,
            color=target.color,
        )

    def _handle_fader(
        self,
        target: FeedbackTarget,
        *,
        value: Any,
        raw_value: int | None,
        db_value: float | None,
    ) -> dict:
        msg = self._apply_style(
            target.hwcid,
            True,
            mode=target.mode,
            color=target.color,
        )
        if raw_value is not None:
            msg.update(self._panel._move_fader(target.hwcid, raw_value))
        return msg

    def _handle_display(
        self,
        target: FeedbackTarget,
        *,
        value: Any,
        raw_value: int | None,
        db_value: float | None,
    ) -> dict:
        txt = target.text or {}
        return self._panel._set_text(
            target.hwcid,
            text1=db_value,
            **txt,
        )

    async def _process_data_feedback(self, d: dict) -> None:
        # TODO: Consider waking the panel
        # TODO: Only change fader style and display txt during feedback init
        messages = []
        for path, value in d.items():
            mapping = self._map.resolve(path)
            if mapping is None:
                logger.debug("path %s is not mapped", path)
                continue
            db_value = None
            raw_value = None
            tail = path.rsplit("/", 1)[-1]
            if tail in ("mute", "fader", "send"):
                self._update_meter_multiplier_for_path(path)
                if tail in ("send", "fader"):
                    db_value = motu.level_to_db(value)
                    raw_value = db_to_raw(db_value, self._faders_range_mapping)
            for target in mapping:
                handler = self._kind_handlers.get(target.kind)
                if handler is None:
                    logger.debug(
                        "Unhandled target kind %s for path %s",
                        target.kind,
                        path,
                    )
                    continue
                msg = handler(
                    target,
                    value=value,
                    raw_value=raw_value,
                    db_value=db_value,
                )
                if msg:
                    messages.append(msg)
        batched = self._panel.batch_hwc(messages)
        if batched is not None:
            await self._panel.send(batched)

    def _calculate_meter_multiplier(self, target: FeedbackTarget) -> float | None:
        if target.pre_fader:
            return 1.0
        try:
            fader = self._ds.get(target.fader_path)
            muted = self._ds.get(target.mute_path)
        except KeyError:
            logger.warning("fader_path and mute_path required when pre_fader is False")
            return None
        return float(fader) * float(not muted)

    @staticmethod
    def _get_meter_sample(samples: list | None, index: int | None) -> float | None:
        try:
            return samples[index]
        except (KeyError, IndexError, TypeError):
            return None

    def _get_channel_by_slot(self, channels: tuple[int, ...], slot: int) -> int | None:
        """Return the channels[slot], or None if missing."""
        try:
            return channels[slot]
        except IndexError:
            return None

    def _meter_level_to_raw(self, value: int, multiplier: float = 1.0) -> int:
        """Meter sample (0–1000 scale) → panel raw."""
        linear = value * multiplier / 1000
        db = motu.level_to_db(linear)
        return db_to_raw(db, self._meters_range_mapping)

    @staticmethod
    def _split_meter_payload(
        data: dict,
    ) -> tuple[list | None, list | None]:
        """Return (level_samples, peak_samples) from a meters diff."""
        level_samples = None
        peak_samples = None
        for key, samples in data.items():
            if not key.startswith("mix/level"):
                continue
            suffix = key.rsplit("/", 1)[-1]
            if suffix == "peaks":
                peak_samples = samples
            else:
                level_samples = samples
        return level_samples, peak_samples

    def _get_meter_multiplier(self, target: FeedbackTarget) -> float:
        try:
            return self._meter_multipliers[target.hwcid]
        except KeyError:
            multiplier = self._calculate_meter_multiplier(target)
            if multiplier is None:
                return 1.0
            self._meter_multipliers[target.hwcid] = multiplier
            return multiplier

    def _is_channel_changed(
        self,
        ch: int,
        levels: list | None,
        prev_levels: list | None,
        peaks: list | None,
        prev_peaks: list | None,
    ) -> bool:
        if prev_levels is None or self._get_meter_sample(
            levels, ch
        ) != self._get_meter_sample(prev_levels, ch):
            return True
        if peaks is None or prev_peaks is None:
            return peaks is not None
        return self._get_meter_sample(peaks, ch) != self._get_meter_sample(
            prev_peaks, ch
        )

    def _bundle_meter_samples(
        self,
        level_samples: list | None,
        peak_samples: list | None,
        channels: tuple[int, ...],
        multiplier: float,
    ) -> list:
        bundle = []
        for samples in (level_samples, peak_samples):
            for slot in (0, 1):
                channel = self._get_channel_by_slot(channels, slot)
                sample = self._get_meter_sample(samples, channel)
                bundle.append(
                    None
                    if sample is None
                    else self._meter_level_to_raw(sample, multiplier)
                )
        return bundle

    async def _process_meters_feedback(self, d: dict) -> None:
        # TODO: Only send audio_meter during feedback init
        self._panel.last_activity = time.perf_counter()
        displays = self._meter_targets
        if not displays:
            logger.warning("path mix/level is not mapped")
            return

        level_samples, peak_samples = self._split_meter_payload(d)
        if level_samples is None:
            return
        prev_levels = self._ms.previous_levels if self._ms else None
        prev_peaks = self._last_peaks
        await self._panel.wakeup()
        messages = []
        for target in displays:
            audio_meter = target.audio_meter
            if audio_meter is None:
                continue
            channels = target.channels or ()
            multiplier = self._get_meter_multiplier(target)
            if multiplier == 0.0 and prev_peaks is not None:
                if target.hwcid in self._silent_meters:
                    continue
                self._silent_meters.add(target.hwcid)
            else:
                self._silent_meters.discard(target.hwcid)
                if prev_levels is not None and not any(
                    self._is_channel_changed(
                        ch,
                        level_samples,
                        prev_levels,
                        peak_samples,
                        prev_peaks,
                    )
                    for ch in channels
                ):
                    continue
            bundle = self._bundle_meter_samples(
                level_samples,
                peak_samples,
                channels,
                multiplier,
            )
            messages.append(
                self._panel._set_audio_meter(
                    target.hwcid,
                    data1=bundle[0],
                    data2=bundle[1],
                    peak1=bundle[2],
                    peak2=bundle[3],
                    **audio_meter,
                )
            )
        batched = self._panel.batch_hwc(messages)
        if batched is not None:
            await self._panel.send(batched)
        if peak_samples is not None:
            self._last_peaks = peak_samples
