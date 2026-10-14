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

    Public path: on_change / init_feedback.
    Private: _process_data_feedback / _process_meters_feedback — currently
    delegate to RawPanel._process_*).
    """

    def __init__(self, feedback_map: FeedbackMap):
        self._map = feedback_map
        self._panel = None

    def set_panel(self, panel) -> None:
        self._panel = panel

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

    async def _process_data_feedback(self, data: dict) -> None:
        await self._panel._process_data_feedback(data)

    async def _process_meters_feedback(self, data: dict) -> None:
        await self._panel._process_meters_feedback(data)
