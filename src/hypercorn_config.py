"""Hypercorn config. Reads LOG_LEVEL because the server sets its own level
after the app is imported, which overwrites anything api.py does."""

import os

_VALID = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
loglevel = _name if _name in _VALID else "INFO"

startup_timeout = 500

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "hypercorn.error": {"handlers": [], "propagate": True},
        "hypercorn.access": {"handlers": [], "propagate": True},
    },
}
