from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Meter samples are kept in a ring buffer of this many slots, and
# current_index points at the newest one. A sample runs from 0 to METER_SCALE,
# which corresponds to linear gain 0 to 1.
METER_SLOTS = 15
METER_SCALE = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def dict_diff(d_old: dict, d_new: dict) -> dict:
    """Keys from d_new that are new or different."""
    return {k: v for k, v in d_new.items() if k not in d_old or d_old[k] != v}


def generate_client_id() -> int:
    return random.getrandbits(32)


@dataclass(slots=True)
class _HttpResult:
    """Minimal response shape used by _Store / DataStore. 304 has no body."""

    status_code: int
    reason: str | None
    etag: str | None = None
    _body: Any = None

    def json(self) -> Any:
        return self._body


class HTTPClient:
    """Shared aiohttp session with retries and Motu-oriented defaults."""

    def __init__(
        self,
        *,
        retry_interval: float,
        connector_limit: int,
    ) -> None:
        self._retry_interval = retry_interval
        self._connector_limit = connector_limit
        self._session: aiohttp.ClientSession | None = None

    @property
    def started(self) -> bool:
        return self._session is not None and not self._session.closed

    async def start(self) -> None:
        if self.started:
            return
        timeout = aiohttp.ClientTimeout(total=None)
        connector = aiohttp.TCPConnector(
            limit=self._connector_limit,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            # Motu speaks plain HTTP on LAN; keep defaults otherwise.
            raise_for_status=False,
        )
        logger.info("HTTP client session started")

    async def close(self) -> None:
        if self.started:
            await self._session.close()
            logger.info("HTTP client session closed")
        self._session = None

    async def request(
        self,
        url: str,
        *,
        params: dict | None = None,
        etag: str | None = None,
        method: str = "GET",
        data: bytes | str | None = None,
        retries: int | None = None,
        attempt_timeout: float | None = None,
    ) -> _HttpResult | None:
        """Send a request, retrying failed attempts.

        Returns the result for 200 and 204, and for 304 (not modified, with no
        body). Returns None when the request failed: any other status or a
        connection error or timeout counts as a failed attempt, which is retried
        up to `retries` times (None retries forever) with `retry_interval`
        seconds between attempts. Retries never send the ETag.
        """
        if not self.started:
            logger.error("HTTP request attempted before session start: %s", url)
            return None

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if method.upper() == "PATCH":
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        client_timeout = aiohttp.ClientTimeout(total=attempt_timeout)
        attempt = 0

        while True:
            attempt += 1
            try:
                assert self._session is not None
                async with self._session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    data=data,
                    timeout=client_timeout,
                ) as resp:
                    status = resp.status
                    reason = resp.reason
                    resp_etag = resp.headers.get("ETag")  # case-insensitive

                    if status in (200, 204, 304):
                        body = None
                        if status == 200:
                            body = await resp.json(content_type=None)
                        return _HttpResult(status, reason, resp_etag, body)
                    failure = f"HTTP {status} {reason}"
            except (aiohttp.ClientError, TimeoutError) as e:
                failure = type(e).__name__
                logger.debug("%s", e)

            logger.warning("Request to %s failed (%s)", url, failure)
            # The MOTU resets its ETag sequence on reboot, so a stale ETag can
            # keep failing. Retry without it to fetch the full state.
            headers.pop("If-None-Match", None)
            if retries is not None and attempt > retries:
                logger.error("Maximum retries reached for %s", url)
                return None
            await asyncio.sleep(self._retry_interval)


class _Store:
    def __init__(
        self,
        http: HTTPClient,
        *,
        host: str,
        port: int,
        refresh_timeout: float | None,
        refresh_retries: int | None,
    ) -> None:
        """Timeout and retries apply to every refresh() request, both the
        initial one at startup and those made by poll(). None means no
        timeout / unlimited retries."""
        self.http = http
        self.host = host
        self.port = port
        self.refresh_timeout = refresh_timeout
        self.refresh_retries = refresh_retries
        self.base_path = ""
        self.refresh_params: dict | None = None
        self.etag: str | None = None
        self.client_id: int | None = None
        self.data: dict = {}
        self._change_controller = None
        self._stopped = False

    @property
    def idle(self) -> bool:
        return False

    def _url(self) -> str:
        return f"http://{self.host}:{self.port}/{self.base_path}"

    def stop(self) -> None:
        """Signal poll loops to exit on next iteration."""
        self._stopped = True

    async def refresh(
        self,
        *,
        diff_check: bool = False,
        handle_changes: bool = True,
    ) -> dict | None:
        params: dict = {}
        if self.refresh_params:
            params.update(self.refresh_params)
        params["client"] = self.client_id

        response = await self.http.request(
            self._url(),
            params=params,
            etag=self.etag,
            retries=self.refresh_retries,
            attempt_timeout=self.refresh_timeout,
        )
        if response is None:
            self.etag = None
            return None
        if response.status_code == 304:
            logger.debug("Not modified: %s", self.base_path)
            return None

        payload = response.json() or {}
        data_diff = dict_diff(self.data, payload) if diff_check else payload

        etag = response.etag
        if etag:
            self.etag = etag
        else:
            logger.warning(
                "Missing ETag on %s response",
                self.base_path,
            )
        if data_diff:
            self.data.update(data_diff)
            logger.debug("Modified: %s -> %s", self.base_path, data_diff)
            if self._change_controller and handle_changes:
                await self._change_controller.on_change(
                    data_diff, source=self.base_path
                )
            return data_diff
        return None

    def get(self, path: str) -> Any | None:
        return self.data.get(path)

    async def poll(
        self,
        *,
        diff_check: bool = True,
        handle_changes: bool = True,
        idle_interval: float = 0.0,
    ) -> None:
        logger.info("Polling MOTU %s (%s:%s)...", self.base_path, self.host, self.port)
        while not self._stopped:
            try:
                started = time.perf_counter()
                diff = await self.refresh(
                    diff_check=diff_check,
                    handle_changes=handle_changes,
                )
                interval = 0.0 if (diff or not self.idle) else idle_interval
                wait = max(0, interval - (time.perf_counter() - started))
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                break
        logger.info(
            "Stopped polling MOTU %s (%s:%s)",
            self.base_path,
            self.host,
            self.port,
        )

    def set_change_controller(self, controller: Any) -> None:
        """Attach controller implementing on_change(data, *, source, origin=None)."""
        self._change_controller = controller


class DataStore(_Store):
    def __init__(
        self,
        http: HTTPClient,
        *,
        host: str,
        port: int,
        refresh_timeout: float | None,
        refresh_retries: int | None,
        mutate_timeout: float | None,
        mutate_retries: int | None,
    ) -> None:
        super().__init__(
            http,
            host=host,
            port=port,
            refresh_timeout=refresh_timeout,
            refresh_retries=refresh_retries,
        )
        self.mutate_timeout = mutate_timeout
        self.mutate_retries = mutate_retries
        self.base_path = "datastore"
        self.client_id = generate_client_id()

    async def set(
        self, path: str, value: Any, *, origin: str | None = None
    ) -> Any | None:
        data = f"json={json.dumps({path: value})}".encode()
        response = await self.http.request(
            self._url(),
            params={"client": self.client_id},
            method="PATCH",
            data=data,
            retries=self.mutate_retries,
            attempt_timeout=self.mutate_timeout,
        )
        # 204 has no body, so the sent value is the result. 200 is accepted
        # the same way: request() only returns for 200/204.
        if response is None or response.status_code not in (200, 204):
            return None
        data_diff = {path: value}
        self.data.update(data_diff)
        logger.debug("Modified: %s -> %s", self.base_path, data_diff)
        if self._change_controller:
            await self._change_controller.on_change(
                data_diff, source=self.base_path, origin=origin
            )
        return value

    async def toggle(self, path: str) -> float | None:
        current = self.get(path)
        if current is None:
            return None
        return await self.set(path, float(not current))


class Meters(_Store):
    def __init__(
        self,
        http: HTTPClient,
        *,
        host: str,
        port: int,
        refresh_timeout: float | None,
        refresh_retries: int | None,
    ) -> None:
        super().__init__(
            http,
            host=host,
            port=port,
            refresh_timeout=refresh_timeout,
            refresh_retries=refresh_retries,
        )
        self.base_path = "meters"
        self.refresh_params = {"meters": "mix/level"}
        self.client_id = generate_client_id()
        self.current_index: int | None = None

    @property
    def previous_levels(self) -> list | None:
        """Previous meter samples, or None when everything should be sent."""
        if self.current_index is None:
            return None
        return self.get(f"mix/level/{(self.current_index - 1) % METER_SLOTS}")

    @property
    def idle(self) -> bool:
        """True when every peak is 0. False if peaks are not populated yet."""
        peaks = self.data.get("mix/level/peaks")
        if not peaks:
            return False  # not populated yet
        return not any(peaks)

    def _update_peaks(self) -> dict:
        """Peak of each channel across the METER_SLOTS sample slots."""
        filtered_data = {k: v for k, v in self.data.items() if not k.endswith("peaks")}
        if not filtered_data:
            return {}
        peaks = {
            "mix/level/peaks": [
                max(values) for values in zip(*filtered_data.values(), strict=True)
            ]
        }
        self.data.update(peaks)
        logger.debug("Modified: %s -> %s", self.base_path, peaks)
        return peaks

    def _set_current_index(self, data: dict) -> None:
        for key in data:
            if not key.startswith("mix/level/") or key.endswith("peaks"):
                continue
            try:
                self.current_index = int(key.rsplit("/", 1)[-1])
            except ValueError:
                continue
            return

    async def refresh(
        self,
        *,
        diff_check: bool = True,
        handle_changes: bool = True,
    ) -> dict | None:
        data_diff = await super().refresh(
            diff_check=diff_check,
            handle_changes=False,
        )
        if data_diff:
            self._set_current_index(data_diff)
            data_diff.update(self._update_peaks())
            if self._change_controller and handle_changes:
                await self._change_controller.on_change(
                    data_diff, source=self.base_path
                )
            return data_diff
        return None
