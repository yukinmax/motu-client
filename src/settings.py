"""Application settings.

Priority, highest first: MOTU_BRIDGE_<KEY> environment variables, the optional
TOML file named by MOTU_BRIDGE_CONFIG, then the defaults below. A missing or
unset config file is skipped.

Conventions: a timeout of 0 disables the timeout, and a retry count of -1
means retry forever. Both are exposed as None on the settings object.
"""

import os
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

Port = Annotated[int, Field(ge=1, le=65535)]
Interval = Annotated[float, Field(ge=0)]  # 0 means no wait
Timeout = Annotated[float, Field(ge=0), AfterValidator(lambda v: v or None)]
Retries = Annotated[int, Field(ge=-1), AfterValidator(lambda v: v if v >= 0 else None)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOTU_BRIDGE_", extra="forbid", validate_default=True
    )

    # Devices and API
    motu_host: str = "ultralite-avb"
    motu_port: Port = 80
    panel_host: str = "waveboard"
    panel_port: Port = 9923
    api_host: str = "0.0.0.0"
    api_port: Port = 5000
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"

    # MOTU HTTP client
    # Apply to every refresh() request, at startup and in the poll loop.
    # The MOTU holds a long-poll for ~10 s.
    motu_refresh_timeout: Timeout = 15
    motu_refresh_retries: Retries = -1
    motu_mutate_timeout: Timeout = 2
    motu_mutate_retries: Retries = 0
    motu_retry_interval: Interval = 10
    motu_connector_limit: Annotated[int, Field(ge=1)] = 10
    meters_idle_interval: Interval = 0.2

    # Panel connection
    panel_connect_retries: Retries = 20
    panel_connect_retry_interval: Interval = 10
    panel_connect_timeout: Timeout = 10
    panel_send_timeout: Timeout = 1
    panel_wakeup_min_interval: Interval = 10

    # Panel behavior
    panel_delay: Interval = 0.001
    panel_idle_sleep: Annotated[float, Field(gt=0)] = 0.1
    panel_sleep_timeout: Annotated[int, Field(ge=0)] = 600
    panel_sleep_mode: Annotated[int, Field(ge=0, le=1)] = 0
    panel_sleep_screen_saver: Annotated[int, Field(ge=0, le=3)] = 0
    panel_brightness_leds: Annotated[int, Field(ge=0, le=8)] = 4
    panel_brightness_oleds: Annotated[int, Field(ge=0, le=8)] = 2
    panel_dimmed_gain: Annotated[int, Field(ge=0, le=64)] = 20

    # Server lifecycle
    startup_timeout: Timeout = 500
    shutdown_timeout: Annotated[float, Field(gt=0)] = 5

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value):
        return value.upper() if isinstance(value, str) else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml = TomlConfigSettingsSource(
            settings_cls, toml_file=os.environ.get("MOTU_BRIDGE_CONFIG")
        )
        return init_settings, env_settings, toml


try:
    settings = Settings()
except ValidationError as exc:
    raise SystemExit(f"Invalid configuration:\n{exc}") from None
