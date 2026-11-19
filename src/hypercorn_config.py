"""Hypercorn config, loaded with --config file:hypercorn_config.py.

Run Hypercorn as `python -m hypercorn` from this directory so that the
settings module is importable.
"""

from settings import settings

bind = [f"{settings.api_host}:{settings.api_port}"]
loglevel = settings.log_level
startup_timeout = settings.startup_timeout

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "hypercorn.error": {"handlers": [], "propagate": True},
        "hypercorn.access": {"handlers": [], "propagate": True},
    },
}
