from __future__ import annotations

import aiohttp
import asyncio
import json
import logging
import math
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

level_range = (0, 10 ** (12 / 20))

# Motu long-poll hold is ~15s; client timeout slightly above that.
DEFAULT_POLL_TIMEOUT_SEC = 20
DEFAULT_MUTATE_TIMEOUT_SEC = 5
DEFAULT_MUTATE_RETRIES = 1
DEFAULT_RETRY_INTERVAL_SEC = 10
DEFAULT_HOSTNAME = "ultralite-avb.local"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def limit_to_range(value: float, minimum: float, maximum: float) -> float:
    return max(min(value, maximum), minimum)


def level_to_db(value: float) -> float:
    if value == 0:
        return -math.inf
    return round(20 * math.log10(value), 2)


def level_from_db(db_value: float) -> float:
    db_value = float(db_value)
    if db_value > 12:
        db_value = 12
    elif db_value < -120:
        db_value = -math.inf
    return 10 ** (db_value / 20)


# TODO: Consider splitting into 2 functions
def db_from_raw(
    value: int | float,
    range_mapping: tuple[tuple, ...],
    reverse: bool = False,
    ) -> float | int:
    from_index = int(reverse)
    to_index = int(not reverse)
    type_conversion = int if reverse else float
    try:
        from_min_abs = range_mapping[0][from_index][0]
        to_min_abs = range_mapping[0][to_index][0]
    except TypeError:
        from_min_abs = range_mapping[0][from_index]
        to_min_abs = range_mapping[0][to_index]
    try:
        from_max_abs = range_mapping[-1][from_index][1]
        to_max_abs = range_mapping[-1][to_index][1]
    except TypeError:
        from_max_abs = range_mapping[-1][from_index]
        to_max_abs = range_mapping[-1][to_index]
    value = limit_to_range(value, from_min_abs, from_max_abs)
    for i in range_mapping:
        try:
            from_min = i[from_index][0]
            from_max = i[from_index][1]
            to_min = i[to_index][0]
            to_max = i[to_index][1]
        except TypeError:
            if value == i[from_index]:
                return type_conversion(i[to_index])
        else:
            if from_min <= value < from_max:
                break
    cr = (to_max - to_min) / (from_max - from_min)
    out_value = ((value - from_min) * cr) + to_min
    out_value = limit_to_range(out_value, to_min_abs, to_max_abs)
    return type_conversion(out_value)


def dict_diff(d_old: dict, d_new: dict) -> dict:
    return dict(set(d_new.items()) - set(d_old.items()))


def dict_values_to_tuples(d: dict) -> dict:
    d_new = {}
    for k, v in d.items():
        try:
            d_new[k] = tuple(v)
        except TypeError:
            d_new[k] = v
    return d_new


def generate_client_id() -> int:
    return random.getrandbits(32)


@dataclass(slots=True)
class HttpResult:
    """Minimal response shape used by Store / DataStore."""

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
        retry_interval_sec: float = DEFAULT_RETRY_INTERVAL_SEC,
        connector_limit: int = 10,
    ) -> None:
        self._retry_interval_sec = retry_interval_sec
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
        timeout: float | None = None,
    ) -> HttpResult | None:
        if not self.started:
            logger.error(
                "HTTP request attempted before session start: %s", url
            )
            return None

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if method.upper() == "PATCH":
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        client_timeout = aiohttp.ClientTimeout(total=timeout)
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
                    etag = resp.headers.get("ETag")  # case-insensitive

                    if status in (200, 204):
                        body = None
                        if status == 200:
                            body = await resp.json(content_type=None)
                        return HttpResult(status, reason, etag, body)

                    if status != 304:
                        logger.error("Error code %s - %s", status, reason)
                    return None

            except (aiohttp.ClientError, TimeoutError) as e:
                logger.warning(
                    "Request to %s failed (%s)",
                    url,
                    type(e).__name__,
                )
                logger.debug("%s", e)
                if retries is not None and attempt > retries:
                    logger.error("Maximum retries reached for %s", url)
                    return None
                await asyncio.sleep(self._retry_interval_sec)


class Store:
    def __init__(
        self,
        http: HTTPClient,
        hostname: str = DEFAULT_HOSTNAME,
        *,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT_SEC,
    ) -> None:
        self.http = http
        self.hostname = hostname
        self.poll_timeout = poll_timeout
        self.base_path = ""
        self.refresh_params: dict | None = None
        self.etag: str | None = None
        self.client_id: int | None = None
        self.data: dict = {}
        self.change_handler = None

    def _url(self) -> str:
        return f"http://{self.hostname}/{self.base_path}"

    async def refresh(
        self,
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
            timeout=self.poll_timeout,
        )
        if response is None:
            logger.debug("Not modified: %s", self.base_path)
            return None

        payload = response.json() or {}
        if diff_check:
            new_data = dict_values_to_tuples(payload)
            data_diff = dict_diff(self.data, new_data)
        else:
            data_diff = dict_values_to_tuples(payload)

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
            if self.change_handler and handle_changes:
                await self.change_handler(data_diff)
            return data_diff
        return None

    def get(self, path: str):
        return self.data[path]

    async def poll(
        self,
        diff_check: bool = True,
        handle_changes: bool = True,
    ) -> None:
        logger.info("Polling MOTU %s (%s)...", self.base_path, self.hostname)
        while True:
            try:
                await self.refresh(
                    diff_check=diff_check,
                    handle_changes=handle_changes,
                )
                # Yield so other tasks run if Motu answers immediately.
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break

    def set_change_handler(self, handler) -> None:
        self.change_handler = handler


class DataStore(Store):
    def __init__(
        self,
        http: HTTPClient,
        hostname: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(http, hostname or DEFAULT_HOSTNAME, **kwargs)
        self.base_path = "datastore"
        self.client_id = generate_client_id()

    async def set(self, path: str, value) -> HttpResult | None:
        data = f"json={json.dumps({path: value})}".encode()
        response = await self.http.request(
            self._url(),
            params={"client": self.client_id},
            method="PATCH",
            data=data,
            retries=DEFAULT_MUTATE_RETRIES,
            timeout=DEFAULT_MUTATE_TIMEOUT_SEC,
        )
        if response is not None:
            data_diff = {path: value}
            self.data.update(data_diff)
            logger.debug("Modified: %s -> %s", self.base_path, data_diff)
            if self.change_handler:
                await self.change_handler({path: value})
        return response

    async def toggle(self, path: str):
        try:
            s = self.get(path)
        except KeyError:
            return "FAILURE"
        j = float(not s)
        r = await self.set(path, j)
        if r is not None and r.status_code == 204:
            return j
        logger.error("Failed to toggle %s", path)
        return "FAILURE"


class Meters(Store):
    def __init__(
        self,
        http: HTTPClient,
        hostname: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(http, hostname or DEFAULT_HOSTNAME, **kwargs)
        self.base_path = "meters"
        self.refresh_params = {"meters": "mix/level"}
        self.client_id = generate_client_id()

    def update_peaks(self) -> dict:
        filtered_data = {
            k: v for k, v in self.data.items() if not k.endswith("peaks")
        }
        if not filtered_data:
            return {}
        peaks = {
            "mix/level/peaks": tuple(
                max(values) for values in zip(*filtered_data.values())
            )
        }
        self.data.update(peaks)
        logger.debug("Modified: %s -> %s", self.base_path, peaks)
        return peaks

    async def refresh(
        self,
        diff_check: bool = True,
        handle_changes: bool = True,
    ) -> dict | None:
        data_diff = await super().refresh(
            diff_check=diff_check,
            handle_changes=False,
        )
        if data_diff:
            data_diff.update(self.update_peaks())
            if self.change_handler and handle_changes:
                await self.change_handler(data_diff)
            return data_diff
        return None
