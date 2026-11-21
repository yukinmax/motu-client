"""MOTU mixer controls, described by the device's own mix/ctrls specs.

Every value under mix/ in the datastore resolves to a Control. Its codec,
built from the matching mix/ctrls spec, validates and converts values between
the API and the datastore. Values without a spec are read-only.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

import levels

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MixerError(Exception):
    """Base class for errors raised by MixerService."""


class NotLoaded(MixerError):
    pass


class NotFound(MixerError):
    pass


class ReadOnly(MixerError):
    pass


class InvalidValue(MixerError):
    pass


class NotToggleable(MixerError):
    pass


class Rejected(MixerError):
    pass


# ---------------------------------------------------------------------------
# Codecs
# ---------------------------------------------------------------------------


def _number(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        raise InvalidValue("'value' must be a finite number")
    return float(value)


def _json_number(value: float) -> float | None:
    """JSON has no infinity or NaN, so -inf (silence in dB) becomes null."""
    return round(value, 10) if math.isfinite(value) else None


class Codec(Protocol):
    @property
    def formats(self) -> tuple[str, ...]: ...

    @property
    def toggleable(self) -> bool: ...

    def decode(self, stored: Any, fmt: str | None) -> Any: ...

    def encode(self, value: object, fmt: str | None) -> float: ...


@dataclass(frozen=True, slots=True)
class BoolCodec:
    formats: ClassVar[tuple[str, ...]] = ()
    toggleable: ClassVar[bool] = True

    def decode(self, stored: Any, fmt: str | None) -> bool:
        return bool(stored)

    def encode(self, value: object, fmt: str | None) -> float:
        if not isinstance(value, bool):
            raise InvalidValue("'value' must be true or false")
        return float(value)


@dataclass(frozen=True, slots=True)
class NumberCodec:
    minimum: float
    maximum: float
    unit: str | None = None
    formats: ClassVar[tuple[str, ...]] = ()
    toggleable: ClassVar[bool] = False

    def decode(self, stored: Any, fmt: str | None) -> float | None:
        return _json_number(float(stored))

    def encode(self, value: object, fmt: str | None) -> float:
        number = _number(value)
        if not self.minimum <= number <= self.maximum:
            raise InvalidValue(
                f"'value' must be between {self.minimum:g} and {self.maximum:g}"
            )
        return number


@dataclass(frozen=True, slots=True)
class EnumCodec:
    options: Mapping[int, str]
    formats: ClassVar[tuple[str, ...]] = ()
    toggleable: ClassVar[bool] = False

    def decode(self, stored: Any, fmt: str | None) -> int:
        return int(stored)

    def encode(self, value: object, fmt: str | None) -> float:
        if isinstance(value, bool) or value not in self.options:
            choices = ", ".join(f"{k} ({v})" for k, v in self.options.items())
            raise InvalidValue(f"'value' must be one of: {choices}")
        return float(value)


class Scale(Protocol):
    """A level scale other than linear gain, such as a panel's fader scale."""

    def to_db(self, value: float) -> float: ...

    def from_db(self, db: float) -> float: ...


@dataclass(frozen=True, slots=True)
class LevelCodec:
    """Linear gain, also readable and writable in dB and any extra scales."""

    minimum: float
    maximum: float
    scales: Mapping[str, Scale] = field(default_factory=dict)
    toggleable: ClassVar[bool] = False

    @property
    def formats(self) -> tuple[str, ...]:
        return ("linear", "db", *self.scales)

    def decode(self, stored: Any, fmt: str | None) -> float | None:
        level = float(stored)
        if fmt == "linear":
            return _json_number(level)
        db = levels.level_to_db(level)
        if fmt == "db":
            return _json_number(db)
        return _json_number(self.scales[fmt].from_db(db))

    def encode(self, value: object, fmt: str | None) -> float:
        if fmt == "linear":
            level = levels.limit_to_range(_number(value), self.minimum, self.maximum)
        elif fmt == "db" and value is None:
            level = self.minimum  # null is silence, as in responses
        else:
            db = _number(value)
            if fmt != "db":
                db = self.scales[fmt].to_db(db)
            level = levels.db_to_level(
                levels.limit_to_range(
                    db,
                    levels.level_to_db(self.minimum),
                    levels.level_to_db(self.maximum),
                )
            )
        return round(level, levels.LEVEL_DECIMALS)


def parse_spec(spec: str, scales: Mapping[str, Scale] | None = None) -> Codec:
    """Codec for a mix/ctrls spec, with any extra level scales.

    Specs look like 'bool:0', 'enum:0:0=RMS:1=Peak', 'real:2:1:10',
    'int:100:20:20000:Hz' and 'real:0:0:4:linear' (default first).
    """
    kind, _default, *rest = spec.split(":")
    if kind == "bool":
        return BoolCodec()
    if kind == "enum":
        return EnumCodec({int(k): v for k, v in (o.split("=", 1) for o in rest)})
    minimum, maximum, *unit = rest
    if unit == ["linear"]:
        return LevelCodec(float(minimum), float(maximum), scales or {})
    return NumberCodec(float(minimum), float(maximum), unit[0] if unit else None)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Datastore path -> mix/ctrls key, first match wins. Sends and pans towards
# another bus share the matrix fader and pan specs.
CTRL_RULES = (
    (re.compile(r"^mix/talkback/(\w+)$"), r"talkback/\1"),
    (re.compile(r"^mix/\w+/\d+/matrix/\w+/\d+/send$"), "matrix/fader"),
    (re.compile(r"^mix/\w+/\d+/matrix/\w+/\d+/pan$"), "matrix/pan"),
    (re.compile(r"^mix/\w+/\d+/(.+)$"), r"\1"),
)


@dataclass(frozen=True, slots=True)
class Control:
    path: str
    codec: Codec | None  # None: read-only


class Controls:
    """Datastore path -> Control, using the specs found in the datastore."""

    def __init__(self, store: Store, scales: Mapping[str, Scale] | None = None) -> None:
        self._store = store
        self._scales = dict(scales or {})
        self._codecs: dict[str, Codec | None] = {}

    def _ctrl_key(self, path: str) -> str | None:
        for pattern, key in CTRL_RULES:
            if pattern.match(path):
                return pattern.sub(key, path)
        return None

    def _codec(self, path: str) -> Codec | None:
        key = self._ctrl_key(path)
        if key is None:
            return None
        # mix/ctrls/<strip> lists the sections a strip type supports.
        sections = self._store.get(f"mix/ctrls/{path.split('/')[1]}")
        if isinstance(sections, str) and key.split("/")[0] not in sections.split(":"):
            return None
        spec = self._store.get(f"mix/ctrls/{key}")
        return parse_spec(spec, self._scales) if isinstance(spec, str) else None

    def get(self, path: str) -> Control | None:
        """The control at path, or None if the datastore has no such value."""
        if not path.startswith("mix/") or path not in self._store.data:
            return None
        if path not in self._codecs:
            self._codecs[path] = self._codec(path)
        return Control(path, self._codecs[path])


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Store(Protocol):
    """The datastore operations MixerService needs (motu.DataStore)."""

    data: dict

    def get(self, path: str) -> Any | None: ...

    async def set(self, path: str, value: Any) -> Any | None: ...

    async def toggle(self, path: str) -> float | None: ...


@dataclass(frozen=True, slots=True)
class Reading:
    value: Any
    format: str | None = None


class MixerService:
    """Reads and writes mixer values with validation and unit conversion."""

    def __init__(
        self, store: Store, *, scales: Mapping[str, Scale] | None = None
    ) -> None:
        """scales: extra level formats by name, besides linear and db."""
        self._store = store
        self._controls = Controls(store, scales)

    def _control(self, path: str) -> Control:
        if not self._store.data:
            raise NotLoaded("MOTU data has not been loaded yet")
        control = self._controls.get(path)
        if control is None:
            raise NotFound(f"No such MOTU path: {path}")
        return control

    @staticmethod
    def _codec(control: Control) -> Codec:
        if control.codec is None:
            raise ReadOnly(f"{control.path} is read-only")
        return control.codec

    @staticmethod
    def _format(control: Control, fmt: str | None) -> str | None:
        formats = control.codec.formats if control.codec else ()
        if not formats:
            if fmt is not None:
                raise InvalidValue(f"{control.path} has no 'format' option")
            return None
        if fmt is None:
            return formats[0]
        if fmt not in formats:
            raise InvalidValue(f"'format' must be one of: {', '.join(formats)}")
        return fmt

    def _reading(self, control: Control, fmt: str | None) -> Reading:
        stored = self._store.get(control.path)
        if control.codec is None:
            return Reading(stored)
        return Reading(control.codec.decode(stored, fmt), fmt)

    def read(self, path: str, fmt: str | None = None) -> Reading:
        control = self._control(path)
        return self._reading(control, self._format(control, fmt))

    async def write(
        self,
        path: str,
        value: object,
        fmt: str | None = None,
        response_fmt: str | None = None,
    ) -> Reading:
        """fmt is the unit of value; the response uses response_fmt, else fmt."""
        control = self._control(path)
        codec = self._codec(control)
        fmt = self._format(control, fmt)
        response_fmt = (
            fmt if response_fmt is None else self._format(control, response_fmt)
        )
        if await self._store.set(path, codec.encode(value, fmt)) is None:
            raise Rejected(f"MOTU did not accept the change to {path}")
        return self._reading(control, response_fmt)

    async def toggle(self, path: str) -> Reading:
        control = self._control(path)
        if not self._codec(control).toggleable:
            raise NotToggleable(f"{path} is not an on/off control")
        if await self._store.toggle(path) is None:
            raise Rejected(f"MOTU did not accept the change to {path}")
        return self._reading(control, None)
