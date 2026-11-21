"""Gain level conversions shared by the panel and the API."""

import math

# The MOTU's level resolution: it stores anything below 5e-7 as 0.
LEVEL_DECIMALS = 6


def limit_to_range(value: float, minimum: float, maximum: float) -> float:
    return max(min(value, maximum), minimum)


def level_to_db(value: int | float) -> float:
    """Linear gain to dB. 0 returns -inf."""
    value = float(value)
    if value == 0:
        return -math.inf
    return 20 * math.log10(value)


def db_to_level(db_value: int | float) -> float:
    """dB to linear gain. -inf returns 0."""
    return 10 ** (float(db_value) / 20)
