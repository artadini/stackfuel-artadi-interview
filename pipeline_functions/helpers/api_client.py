"""HTTP client for the Stackfuel learning-data API.

* Login: ``POST /login`` returns a bearer token with a lifetime. The token is renewed shortly
  before it expires, and once more if the API answers ``401``.
* Retries: temporary failures (``429``, ``5xx`` such as the API's random ``503``, connection
  errors, timeouts, broken or non-JSON bodies) are retried after a pause (``Retry-After`` or an
  exponential backoff). Other ``4xx`` answers are our own mistakes and fail immediately.
* Pagination: ``fetch_all`` follows ``next_token`` until it is ``null`` and returns exactly what
  the API delivered, nothing deduplicated or trimmed. It fails if the number of records does not
  match ``total_count``.
"""

import json
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import PAGE_SIZE
from .logging_utils import get_logger

from ..helpers.extraction_types import (
    ApiError,
    PaginationError,
    TemporaryFailure,
    Extraction,
)

logger = get_logger(__name__)

TEMPORARY_STATUSES = {429, 500, 502, 503, 504}
RENEW_TOKEN_BEFORE_EXPIRY_SECONDS = 60
MAX_PAUSE_SECONDS = 30
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600


class ApiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        max_attempts: int = 6,
        timeout: float = 30,
        sleep=time.sleep,
        clock=time.monotonic,
        opener=urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.max_attempts = max_attempts
        self.timeout = timeout
        self._sleep = sleep  # replaceable, so tests do not wait
        self._clock = clock
        self._open = opener  # replaceable, so tests do not need a network
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.stats = {"requests": 0, "retries": 0}

    # ------------------------------------------------------------------ sending
    def _send_once(self, method: str, path: str, body: dict | None, with_token: bool):
        """One HTTP attempt. Returns the decoded JSON, or raises ``TemporaryFailure`` / ``ApiError``."""
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if with_token and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        self.stats["requests"] += 1
        request = Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code in TEMPORARY_STATUSES:
                raise TemporaryFailure(
                    f"HTTP {error.code}", error.headers.get("Retry-After")
                ) from error
            detail = error.read().decode("utf-8", errors="replace")
            raise ApiError(
                f"HTTP {error.code} for {path}: {detail}", error.code
            ) from error
        except (URLError, OSError, HTTPException, ValueError) as error:
            # URLError/OSError: refused, reset, timeout. HTTPException: truncated body.
            # ValueError: the body is not JSON (for example an HTML error page of a proxy).
            raise TemporaryFailure(repr(error)) from error

    def _pause_seconds(self, attempt: int, retry_after: str | None) -> float:
        """How long to wait before the next attempt: what the server asks for, otherwise 0.5, 1, 2, 4 ... s."""
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), MAX_PAUSE_SECONDS)
            except ValueError:
                pass
        return min(0.5 * 2 ** (attempt - 1), MAX_PAUSE_SECONDS)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        with_token: bool = True,
    ):
        """Send a request, retrying temporary failures up to ``max_attempts`` times."""
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._send_once(method, path, body, with_token)
            except TemporaryFailure as failure:
                if attempt == self.max_attempts:
                    raise ApiError(
                        f"{path} failed after {self.max_attempts} attempts: {failure.reason}"
                    ) from failure
                pause = self._pause_seconds(attempt, failure.retry_after)
                logger.warning(
                    "Temporary API failure, retrying: path=%s failure=%s attempt=%s/%s pause=%.1fs",
                    path,
                    failure.reason,
                    attempt,
                    self.max_attempts,
                    pause,
                )
                self.stats["retries"] += 1
                self._sleep(pause)

    # ------------------------------------------------------------------ login
    def login(self) -> None:
        credentials = {"username": self.username, "password": self.password}
        answer = self._request("POST", "/login", body=credentials, with_token=False)
        token = answer.get("access_token") if isinstance(answer, dict) else None
        if not token:
            raise ApiError("Login response did not contain an access_token")
        lifetime = answer.get("expires_in")
        if not isinstance(lifetime, (int, float)) or lifetime <= 0:
            lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS
        self._token = token
        self._token_expires_at = self._clock() + lifetime
        logger.info(
            "Authenticated with %s (token valid for %ss)", self.base_url, int(lifetime)
        )

    def _token_is_expiring(self) -> bool:
        return (
            self._token is None
            or self._clock()
            >= self._token_expires_at - RENEW_TOKEN_BEFORE_EXPIRY_SECONDS
        )

    def get(self, path: str, query: dict | None = None) -> dict:
        """GET with a valid token. If the API still answers ``401``, log in again and retry once."""
        if self._token_is_expiring():
            self.login()
        full_path = f"{path}?{urlencode(query)}" if query else path
        try:
            return self._request("GET", full_path)
        except ApiError as error:
            if error.status != 401:
                raise
        logger.warning("Token rejected by the API; logging in again")
        self.login()
        return self._request("GET", full_path)

    # ------------------------------------------------------------------ pagination
    @staticmethod
    def _read_page(endpoint: str, response) -> tuple[list[dict], dict]:
        """The records and the pagination info of one response, or ``PaginationError`` if it is malformed."""
        data = response.get("data") if isinstance(response, dict) else None
        pagination = response.get("pagination") if isinstance(response, dict) else None
        if not isinstance(data, list) or not isinstance(pagination, dict):
            raise PaginationError(
                f"{endpoint}: response has no 'data' list or 'pagination' object"
            )
        if any(not isinstance(record, dict) for record in data):
            raise PaginationError(
                f"{endpoint}: page contains a record that is not an object"
            )
        if not isinstance(pagination.get("total_count"), int) or pagination.get(
            "returned"
        ) != len(data):
            raise PaginationError(f"{endpoint}: invalid pagination counts {pagination}")
        return data, pagination

    def fetch_all(
        self, endpoint: str, params: dict | None = None, page_size: int = PAGE_SIZE
    ) -> Extraction:
        """Read every page of ``endpoint`` and return exactly the records the API delivered."""
        query = {**(params or {}), "page_size": page_size}
        result = Extraction()
        seen_tokens: set[str] = set()

        while True:
            records, pagination = self._read_page(endpoint, self.get(endpoint, query))
            result.pages += 1
            if result.pages == 1:
                result.total_count = pagination["total_count"]
            elif pagination["total_count"] != result.total_count:
                raise PaginationError(
                    f"{endpoint}: total_count changed from {result.total_count} to {pagination['total_count']} while reading"
                )
            result.records.extend(records)
            result.page_numbers.extend([result.pages] * len(records))

            next_token = pagination.get("next_token")
            if next_token is None:
                break
            if not isinstance(next_token, str) or next_token in seen_tokens:
                raise PaginationError(f"{endpoint}: invalid or repeated next_token")
            seen_tokens.add(next_token)
            query["page_token"] = next_token

        if len(result.records) != result.total_count:
            raise PaginationError(
                f"{endpoint}: received {len(result.records)} of {result.total_count} records"
            )
        logger.info(
            "Fetched %s: records=%s pages=%s filters=%s",
            endpoint,
            len(result.records),
            result.pages,
            params or {},
        )
        return result
