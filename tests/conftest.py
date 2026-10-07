"""Shared test helpers.

``FakeApi`` replaces ``urlopen``: it speaks the same protocol as ``api/server.py`` (login,
bearer tokens, ``page_size`` / ``page_token`` pagination, inclusive change filters) from
in-memory lists, and can inject failures. No network, no sleeping.
"""

import base64
import io
import json
from datetime import date, datetime
from email.message import Message
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError

import pytest
from openpyxl import Workbook

from pipeline_functions.helpers.api_client import ApiClient

FILTERS = {  # endpoint -> (filter parameter, record field it compares with >=)
    "/participants": ("modified_after", "modified_at"),
    "/enrollments": ("modified_after", "modified_at"),
    "/progress-events": ("since", "event_time"),
    "/survey-responses": ("since", "submitted_at"),
}


class FakeResponse:
    def __init__(self, payload):
        self._body = (
            json.dumps(payload).encode("utf-8")
            if not isinstance(payload, bytes)
            else payload
        )

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status: int, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "http://fake", status, "error", headers, io.BytesIO(b'{"detail": "x"}')
    )


class FakeApi:
    def __init__(
        self,
        data: dict | None = None,
        trainings: list | None = None,
        as_of: str = "2026-09-01",
    ):
        self.data = {endpoint: [] for endpoint in FILTERS} | (data or {})
        self.trainings = trainings if trainings is not None else []
        self.as_of = as_of
        self.requests: list[tuple[str, dict]] = []
        self.fail_with: list = (
            []
        )  # statuses / exceptions returned before the next success
        self.logins = 0
        self.valid_tokens: set[str] = set()
        self.token_lifetime = 3600
        self.after_page = (
            None  # hook(endpoint, page_number) to mutate data mid-pagination
        )
        self.truncate_after_first_page = False
        self._pages_served = {}

    def __call__(self, request, timeout=None):
        url = urlparse(request.full_url)
        path, query = url.path, {
            key: values[0] for key, values in parse_qs(url.query).items()
        }
        if path == "/login":
            self.logins += 1
            token = f"token-{self.logins}"
            self.valid_tokens = {token}
            return FakeResponse(
                {
                    "access_token": token,
                    "token_type": "bearer",
                    "expires_in": self.token_lifetime,
                }
            )
        self.requests.append((path, query))
        if self.fail_with:
            failure = self.fail_with.pop(0)
            if isinstance(failure, Exception):
                raise failure
            raise http_error(failure, "1" if failure == 503 else None)
        if (
            request.get_header("Authorization", "").replace("Bearer ", "")
            not in self.valid_tokens
        ):
            raise http_error(401)
        if path == "/trainings":
            return FakeResponse({"data": self.trainings, "as_of": self.as_of})
        if path not in FILTERS:
            raise http_error(404)
        parameter, field = FILTERS[path]
        rows = self.data[path]
        if parameter in query:
            rows = [row for row in rows if row[field] >= query[parameter]]
        size = int(query.get("page_size", 100))
        offset = (
            int(base64.urlsafe_b64decode(query["page_token"].encode()).decode())
            if "page_token" in query
            else 0
        )
        page = rows[offset : offset + size]
        next_offset = offset + size
        number = self._pages_served[path] = (
            self._pages_served.get(path, 0) + 1 if offset else 1
        )
        more = next_offset < len(rows) and not (
            self.truncate_after_first_page and offset == 0
        )
        response = FakeResponse(
            {
                "data": page,
                "pagination": {
                    "page_size": size,
                    "returned": len(page),
                    "total_count": len(rows),
                    "next_token": (
                        base64.urlsafe_b64encode(str(next_offset).encode()).decode()
                        if more
                        else None
                    ),
                },
            }
        )
        if self.after_page:  # the source changes after this page was served
            self.after_page(path, number)
        return response


def make_client(api: FakeApi, **kwargs) -> ApiClient:
    sleeps: list[float] = []
    client = ApiClient(
        "http://fake", "user", "secret", opener=api, sleep=sleeps.append, **kwargs
    )
    client.sleeps = sleeps
    return client


# ------------------------------------------------------------------ data factories
def participant(i: int, modified_at: str = "2026-03-01T08:00:00", **overrides) -> dict:
    return {
        "participant_id": i,
        "first_name": f"First{i}",
        "last_name": f"Last{i}",
        "email": f"p{i}@example.com",
        "birth_date": "1990-01-01",
        "city": "Berlin",
        "federal_state": "Berlin",
        "funding_type": "Selbstzahler",
        "acquisition_channel": "Google",
        "created_at": "2026-01-01T09:00:00",
        "modified_at": modified_at,
    } | overrides


def enrollment(
    i: int, participant_id: int, modified_at: str = "2026-03-01T08:00:00", **overrides
) -> dict:
    return {
        "enrollment_id": i,
        "participant_id": participant_id,
        "training_id": "T01",
        "cohort_start_date": "2026-02-02",
        "planned_end_date": "2026-06-01",
        "status": "aktiv",
        "status_changed_at": "2026-02-02T09:00:00",
        "created_at": "2026-01-15T09:00:00",
        "modified_at": modified_at,
    } | overrides


def event(
    i: int, enrollment_id: int = 1, event_time: str | None = None, **overrides
) -> dict:
    return {
        "event_id": f"ev-{i:07d}",
        "enrollment_id": enrollment_id,
        "module_id": "T01-M01",
        "event_type": "module_started",
        "event_time": event_time or f"2026-03-01T{i % 24:02d}:00:00Z",
    } | overrides


def survey(
    i: int,
    enrollment_id: int = 1,
    submitted_at: str = "2026-03-02T10:00:00Z",
    **overrides,
) -> dict:
    return {
        "response_id": i,
        "enrollment_id": enrollment_id,
        "survey_type": "wochenfeedback",
        "survey_week": 1,
        "question_key": "zufriedenheit_gesamt",
        "answer_value": "4",
        "submitted_at": submitted_at,
    } | overrides


def training(training_id: str = "T01", **overrides) -> dict:
    return {
        "training_id": training_id,
        "training_name": "Data Analyst – Vollzeit",
        "track": "Data Analyst",
        "variant": "vollzeit",
        "duration_weeks": 16,
        "list_price_eur": 9900,
        "is_active": True,
        "modules": [
            {
                "module_id": f"{training_id}-M01",
                "module_order": 1,
                "module_name": "Intro",
                "estimated_hours": 10,
            },
            {
                "module_id": f"{training_id}-M02",
                "module_order": 2,
                "module_name": "SQL",
                "estimated_hours": 20,
            },
        ],
    } | overrides


def baseline_api() -> FakeApi:
    """A small but complete source system."""
    return FakeApi(
        data={
            "/participants": [
                participant(1, "2026-03-01T08:00:00"),
                participant(2, "2026-03-02T08:00:00"),
            ],
            "/enrollments": [
                enrollment(1, 1, "2026-03-01T08:00:00"),
                enrollment(2, 2, "2026-03-02T08:00:00"),
            ],
            "/progress-events": [
                event(1, 1, "2026-03-01T10:00:00Z"),
                event(2, 2, "2026-03-02T10:00:00Z"),
            ],
            "/survey-responses": [
                survey(1, 1, "2026-03-03T10:00:00Z"),
                survey(2, 2, "2026-03-04T10:00:00Z"),
            ],
        },
        trainings=[training("T01")],
    )


COACH_HEADER = [
    "Teilnehmer*in",
    "E-Mail",
    "Training",
    "Kohorte",
    "Coach",
    "Ampel",
    "Letzter Kontakt",
    "Notizen",
]


def write_workbook(path, rows: list[list] | None = None) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Betreuung"
    sheet.append(COACH_HEADER)
    for row in (
        rows
        if rows is not None
        else [
            [
                "Last1, First1",
                "p1@example.com",
                "DA VZ",
                datetime(2026, 2, 2),
                "Coach A",
                "rot",
                datetime(2026, 8, 25),
                None,
            ],
            [
                "First2 Last2",
                None,
                "Data Analyst VZ",
                "02/2026",
                "Coach B",
                "grün",
                "14.06.26",
                "Notiz",
            ],
        ]
    ):
        sheet.append(row)
    workbook.create_sheet("Legende").append(["rot", "Handlungsbedarf"])
    workbook.save(path)


@pytest.fixture
def api():
    return baseline_api()


@pytest.fixture
def coach_file(tmp_path):
    path = tmp_path / "coach.xlsx"
    write_workbook(path)
    return path


@pytest.fixture
def make_args(tmp_path, coach_file):
    def factory(**overrides):
        values = {
            "layer": "all",
            "snapshot_date": date(2026, 9, 1),
            "full_refresh": False,
            "rebuild_curated": False,
            "duckdb_path": tmp_path / "db" / "stackfuel.duckdb",
            "coach_file": coach_file,
            "page_size": 500,
            "base_url": "http://fake",
        }
        return SimpleNamespace(**(values | overrides))

    return factory
