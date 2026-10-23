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
import math
import time
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Tuple

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

    def keys(self) -> Iterable[str]:
        return self._bindings.keys()


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


class FeedbackMap:
    """Motu path → list of feedback targets."""

    def __init__(self, bindings: Mapping[str, tuple[FeedbackTarget, ...]]):
        self._bindings = dict(bindings)

    @classmethod
    def from_raw(cls, raw: Mapping[str, Mapping[str, Any]]) -> FeedbackMap:
        out: dict[str, tuple[FeedbackTarget, ...]] = {}
        for path, targets in raw.items():
            built: list[FeedbackTarget] = []
            for name, cfg in targets.items():
                channels = cfg.get("channels")
                built.append(FeedbackTarget(
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
                ))
            out[path] = tuple(built)
        return cls(out)

    def resolve(self, path: str) -> tuple[FeedbackTarget, ...] | None:
        return self._bindings.get(path)

    def __contains__(self, path: str) -> bool:
        return path in self._bindings

    def __len__(self) -> int:
        return len(self._bindings)

    def items(self) -> Iterable[Tuple[str, tuple[FeedbackTarget, ...]]]:
        return self._bindings.items()

    def keys(self) -> Iterable[str]:
        return self._bindings.keys()


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
            logger.debug(
                "MotuController: no datastore; ignore source=%s", source
            )
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
            target for target in (self._map.resolve("mix/level") or ())
            if target.kind == "meter"
        )

    def _update_meter_multiplier_for_path(self, path: str) -> None:
        for target in self._meter_targets:
            if path not in (target.fader_path, target.mute_path):
                continue
            self._meter_multipliers[target.hwcid] = (
                self._calculate_meter_multiplier(target)
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
                key: self._pick_value_by_status(val, bool(active))
                for key, val in mode.items()
            }
            msg.update(self._panel._set_mode(hwcid, **resolved))
        if color:
            resolved = {
                key: self._pick_value_by_status(val, bool(active))
                for key, val in color.items()
            }
            msg.update(self._panel._set_color(hwcid, **resolved))
        return msg

    def _handle_button(
        self,
        target: FeedbackTarget,
        *,
        value: Any,
        raw_value: float | None,
        db_value: float | None,
    ) -> dict:
        if target.value is not None:
            active = value == target.value
        else:
            active = value
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
        raw_value: float | None,
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
        raw_value: float | None,
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
                    db_value = motu.level_to_db(float(value))
                    raw_value = motu.db_from_raw(
                        db_value,
                        self._faders_range_mapping,
                        reverse=True,
                    )
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

    def _calculate_meter_multiplier(
        self, target: FeedbackTarget
    ) -> float | None:
        if target.pre_fader:
            return 1.0
        try:
            fader = self._ds.get(target.fader_path)
            muted = self._ds.get(target.mute_path)
        except KeyError:
            logger.warning(
                "fader_path and mute_path required when pre_fader is False"
            )
            return None
        return float(fader) * float(not muted)

    @staticmethod
    def _get_meter_sample(samples: list | None, index: int) -> float | None:
        try:
            return samples[index]
        except (KeyError, IndexError, TypeError):
            return None

    def _get_sample_by_slot(
        self, samples: list | None, channels: tuple[int, ...], slot: int
    ) -> float | None:
        """Return the sample at channels[slot], or None if missing."""
        try:
            ind = channels[slot]
        except IndexError:
            return None
        return self._get_meter_sample(samples, ind)

    def _meter_level_to_raw(
        self, value: int | float, multiplier: float = 1
    ) -> float:
        """Meter sample (0–1000 scale) → panel raw."""
        linear = float(value) * multiplier / 1000
        db = motu.level_to_db(linear)
        return motu.db_from_raw(db, self._meters_range_mapping, reverse=True)

    @staticmethod
    def _split_meter_payload(
        data: dict,
    ) -> tuple[int | None, list | None, list | None]:
        """Return (index, level_samples, peak_samples) from a meters diff."""
        index = None
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
                index = int(suffix)
        return index, level_samples, peak_samples

    def _get_meter_multiplier(self, target: FeedbackTarget) -> float:
        try:
            return self._meter_multipliers[target.hwcid]
        except KeyError:
            multiplier = self._calculate_meter_multiplier(target)
            if multiplier is None:
                return 1.0
            self._meter_multipliers[target.hwcid] = multiplier
            return multiplier

    def _previous_meter_levels(
        self, current_meter_index: int | None
    ) -> list | None:
        """Previous meter samples, or None when everything should be sent."""
        try:
            return self._ms.get(f"mix/level/{(current_meter_index - 1) % 15}")
        except KeyError:
            return None

    def _is_channel_changed(
        self,
        ch: int,
        levels: list | None,
        prev_levels: list | None,
        peaks: list | None,
        prev_peaks: list | None,
    ) -> bool:
        if (
            prev_levels is None
            or self._get_meter_sample(levels, ch)
            != self._get_meter_sample(prev_levels, ch)
        ):
            return True
        if peaks is None or prev_peaks is None:
            return peaks is not None
        return (
            self._get_meter_sample(peaks, ch)
            != self._get_meter_sample(prev_peaks, ch)
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
                sample = self._get_sample_by_slot(samples, channels, slot)
                bundle.append(
                    None if sample is None
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

        (
            current_index,
            level_samples,
            peak_samples,
        ) = self._split_meter_payload(d)
        prev_levels = self._previous_meter_levels(current_index)
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
            messages.append(self._panel._set_audio_meter(
                target.hwcid,
                data1=bundle[0],
                data2=bundle[1],
                peak1=bundle[2],
                peak2=bundle[3],
                **audio_meter,
            ))
        batched = self._panel.batch_hwc(messages)
        if batched is not None:
            await self._panel.send(batched)
        if peak_samples is not None:
            self._last_peaks = peak_samples
