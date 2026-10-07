"""
Stackfuel Learning-Data API (Test-Umgebung für die Bewerbungsaufgabe)

Startet einen lokalen HTTP-Server, der Daten aus zwei Quellsystemen ausliefert:
  * CRM  -> /participants, /enrollments            (Zeitstempel: lokale Zeit Europe/Berlin, ohne Offset)
  * LXP  -> /progress-events, /survey-responses    (Zeitstempel: UTC, Suffix "Z")
  * Kurskatalog -> /trainings

Keine externen Abhängigkeiten – nur Python-Standardbibliothek (>= 3.9).

    python server.py                # http://127.0.0.1:8000
    python server.py --port 9000
    API_FLAKY=0 python server.py    # deaktiviert die zufälligen 503-Antworten

Interaktive Dokumentation: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import random
import secrets
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

USERNAME = "stackfuel"
PASSWORD = "learn-data-2026"
TOKEN_TTL_SECONDS = 60 * 60
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
FLAKY = os.environ.get("API_FLAKY", "1") != "0"
FLAKY_RATE = 0.04  # Anteil der Anfragen, die mit 503 beantwortet werden

_tokens: dict[str, float] = {}
_rng = random.Random(1234)


# ----------------------------------------------------------------------------
# Daten laden
# ----------------------------------------------------------------------------
def _read_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


TRAININGS = json.loads((DATA_DIR / "trainings.json").read_text(encoding="utf-8"))
PARTICIPANTS = _read_csv("participants.csv")
ENROLLMENTS = _read_csv("enrollments.csv")
PROGRESS_EVENTS = _read_csv("progress_events.csv")
SURVEY_RESPONSES = _read_csv("survey_responses.csv")

for p in PARTICIPANTS:
    p["participant_id"] = _to_int(p["participant_id"])
    p["birth_date"] = p["birth_date"] or None
for e in ENROLLMENTS:
    e["enrollment_id"] = _to_int(e["enrollment_id"])
    e["participant_id"] = _to_int(e["participant_id"])
for ev in PROGRESS_EVENTS:
    ev["enrollment_id"] = _to_int(ev["enrollment_id"])
for r in SURVEY_RESPONSES:
    r["response_id"] = _to_int(r["response_id"])
    r["enrollment_id"] = _to_int(r["enrollment_id"])
    r["survey_week"] = _to_int(r["survey_week"])


# ----------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def parse_timestamp(value: str, assume: str) -> str:
    """Normalisiert einen Zeitstempel-Parameter auf das Vergleichsformat der jeweiligen Quelle.

    assume="local": Zielformat 'YYYY-MM-DDTHH:MM:SS' lokale Zeit (CRM). Ein Offset/Z wird nach
                    Europe/Berlin umgerechnet (vereinfachte Regel: Apr–Okt = UTC+2, sonst UTC+1).
    assume="utc":   Zielformat 'YYYY-MM-DDTHH:MM:SSZ' (LXP). Ohne Offset wird UTC angenommen.
    """
    raw = value.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ApiError(
            422,
            f"Ungültiger Zeitstempel '{value}'. Erwartet ISO 8601, z. B. 2026-08-25T00:00:00 "
            f"oder 2026-08-25T00:00:00Z",
        )
    if assume == "local":
        if dt.tzinfo is not None:
            dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
            offset = 2 if 4 <= dt_utc.month <= 10 else 1
            dt = dt_utc + timedelta(hours=offset)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode_token(offset: int, fingerprint: str) -> str:
    payload = f"{offset}:{fingerprint}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_token(token: str, fingerprint: str) -> int:
    try:
        padded = token + "=" * (-len(token) % 4)
        offset_s, fp = base64.urlsafe_b64decode(padded).decode().split(":", 1)
        if fp != fingerprint:
            raise ValueError
        return int(offset_s)
    except Exception:
        raise ApiError(
            400, "Ungültiger oder zu dieser Abfrage nicht passender page_token"
        )


def paginate(rows: list[dict], query: dict, fingerprint_parts: list[str]) -> dict:
    page_size = _to_int(query.get("page_size", [DEFAULT_PAGE_SIZE])[0])
    if page_size is None or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ApiError(422, f"page_size muss zwischen 1 und {MAX_PAGE_SIZE} liegen")
    fingerprint = hashlib.sha1(
        "|".join(fingerprint_parts + [str(page_size)]).encode()
    ).hexdigest()[:8]
    token = query.get("page_token", [None])[0]
    offset = decode_token(token, fingerprint) if token else 0
    if offset < 0 or offset > len(rows):
        raise ApiError(400, "page_token zeigt außerhalb der Ergebnismenge")
    chunk = rows[offset : offset + page_size]
    next_offset = offset + page_size
    return {
        "data": chunk,
        "pagination": {
            "page_size": page_size,
            "returned": len(chunk),
            "total_count": len(rows),
            "next_token": (
                encode_token(next_offset, fingerprint)
                if next_offset < len(rows)
                else None
            ),
        },
    }


def single(query: dict, key: str) -> str | None:
    v = query.get(key)
    return v[0] if v else None


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------
def ep_trainings(query):
    return {"data": TRAININGS, "as_of": "2026-09-01"}


def ep_participants(query):
    rows = PARTICIPANTS
    fp = ["participants"]
    ma = single(query, "modified_after")
    if ma:
        ts = parse_timestamp(ma, "local")
        rows = [r for r in rows if r["modified_at"] >= ts]
        fp.append(ts)
    return paginate(rows, query, fp)


def ep_enrollments(query):
    rows = ENROLLMENTS
    fp = ["enrollments"]
    ma = single(query, "modified_after")
    if ma:
        ts = parse_timestamp(ma, "local")
        rows = [r for r in rows if r["modified_at"] >= ts]
        fp.append(ts)
    tid = single(query, "training_id")
    if tid:
        rows = [r for r in rows if r["training_id"] == tid]
        fp.append(tid)
    pid = _to_int(single(query, "participant_id"))
    if pid is not None:
        rows = [r for r in rows if r["participant_id"] == pid]
        fp.append(str(pid))
    return paginate(rows, query, fp)


def ep_progress_events(query):
    rows = PROGRESS_EVENTS
    fp = ["progress-events"]
    since = single(query, "since")
    if since:
        ts = parse_timestamp(since, "utc")
        rows = [r for r in rows if r["event_time"] >= ts]
        fp.append(ts)
    until = single(query, "until")
    if until:
        ts = parse_timestamp(until, "utc")
        rows = [r for r in rows if r["event_time"] < ts]
        fp.append(ts)
    eid = _to_int(single(query, "enrollment_id"))
    if eid is not None:
        rows = [r for r in rows if r["enrollment_id"] == eid]
        fp.append(str(eid))
    return paginate(rows, query, fp)


def ep_survey_responses(query):
    rows = SURVEY_RESPONSES
    fp = ["survey-responses"]
    since = single(query, "since")
    if since:
        ts = parse_timestamp(since, "utc")
        rows = [r for r in rows if r["submitted_at"] >= ts]
        fp.append(ts)
    st = single(query, "survey_type")
    if st:
        rows = [r for r in rows if r["survey_type"] == st]
        fp.append(st)
    eid = _to_int(single(query, "enrollment_id"))
    if eid is not None:
        rows = [r for r in rows if r["enrollment_id"] == eid]
        fp.append(str(eid))
    return paginate(rows, query, fp)


PROTECTED = {
    "/trainings": ep_trainings,
    "/participants": ep_participants,
    "/enrollments": ep_enrollments,
    "/progress-events": ep_progress_events,
    "/survey-responses": ep_survey_responses,
}


# ----------------------------------------------------------------------------
# OpenAPI
# ----------------------------------------------------------------------------
def _pagination_params():
    return [
        {
            "name": "page_size",
            "in": "query",
            "schema": {
                "type": "integer",
                "default": DEFAULT_PAGE_SIZE,
                "maximum": MAX_PAGE_SIZE,
            },
        },
        {
            "name": "page_token",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Wert von `pagination.next_token` der vorherigen Antwort.",
        },
    ]


OPENAPI = {
    "openapi": "3.0.3",
    "info": {
        "title": "Stackfuel Learning-Data API (Test)",
        "version": "1.0.0",
        "description": (
            "Aggregiert Daten aus dem CRM (Teilnehmende, Teilnahmen) und der Lernplattform LXP "
            "(Lernfortschritt, Feedback).\n\n"
            "**Auth:** `POST /login` (Formular oder JSON: username/password) liefert einen Bearer-Token, "
            f"gültig {TOKEN_TTL_SECONDS // 60} Minuten. Alle anderen Endpoints erwarten "
            "`Authorization: Bearer <token>`.\n\n"
            "**Pagination:** `data` + `pagination.next_token`; `next_token = null` bedeutet letzte Seite. "
            "`total_count` gibt die Gesamtgröße der gefilterten Ergebnismenge an.\n\n"
            "**Zeitstempel:** CRM-Felder (`created_at`, `modified_at`, `status_changed_at`) sind lokale Zeit "
            "Europe/Berlin ohne Offset. LXP-Felder (`event_time`, `submitted_at`) sind UTC mit Suffix `Z`.\n\n"
            "**Verfügbarkeit:** Die Test-API antwortet gelegentlich mit `503 Service Unavailable`. "
            "Clients sollten mit Wartezeit erneut anfragen."
        ),
    },
    "servers": [{"url": "http://127.0.0.1:8000"}],
    "components": {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "Pagination": {
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer"},
                    "returned": {"type": "integer"},
                    "total_count": {"type": "integer"},
                    "next_token": {"type": "string", "nullable": True},
                },
            },
            "Module": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string", "example": "T01-M03"},
                    "module_order": {"type": "integer"},
                    "module_name": {"type": "string"},
                    "estimated_hours": {"type": "integer"},
                },
            },
            "Training": {
                "type": "object",
                "properties": {
                    "training_id": {"type": "string", "example": "T01"},
                    "training_name": {"type": "string"},
                    "track": {"type": "string", "example": "Data Analyst"},
                    "variant": {
                        "type": "string",
                        "enum": ["vollzeit", "teilzeit", "berufsbegleitend"],
                    },
                    "duration_weeks": {"type": "integer"},
                    "list_price_eur": {"type": "integer"},
                    "is_active": {"type": "boolean"},
                    "modules": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Module"},
                    },
                },
            },
            "Participant": {
                "type": "object",
                "properties": {
                    "participant_id": {"type": "integer"},
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "email": {"type": "string"},
                    "birth_date": {
                        "type": "string",
                        "nullable": True,
                        "example": "1991-04-12",
                    },
                    "city": {"type": "string"},
                    "federal_state": {"type": "string"},
                    "funding_type": {
                        "type": "string",
                        "enum": ["Bildungsgutschein", "Selbstzahler", "Firmenkunde"],
                    },
                    "acquisition_channel": {"type": "string"},
                    "created_at": {
                        "type": "string",
                        "example": "2025-03-04T10:15:00",
                        "description": "lokale Zeit Europe/Berlin",
                    },
                    "modified_at": {
                        "type": "string",
                        "example": "2025-06-01T08:00:00",
                        "description": "lokale Zeit Europe/Berlin",
                    },
                },
            },
            "Enrollment": {
                "type": "object",
                "properties": {
                    "enrollment_id": {"type": "integer"},
                    "participant_id": {"type": "integer"},
                    "training_id": {"type": "string"},
                    "cohort_start_date": {"type": "string", "format": "date"},
                    "planned_end_date": {"type": "string", "format": "date"},
                    "status": {
                        "type": "string",
                        "description": "angemeldet | aktiv | pausiert | abgeschlossen | abgebrochen | storniert",
                    },
                    "status_changed_at": {
                        "type": "string",
                        "description": "lokale Zeit Europe/Berlin",
                    },
                    "created_at": {"type": "string"},
                    "modified_at": {"type": "string"},
                },
            },
            "ProgressEvent": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "example": "ev-0001234"},
                    "enrollment_id": {"type": "integer"},
                    "module_id": {"type": "string"},
                    "event_type": {
                        "type": "string",
                        "enum": [
                            "module_started",
                            "exercise_submitted",
                            "quiz_passed",
                            "module_completed",
                        ],
                    },
                    "event_time": {
                        "type": "string",
                        "example": "2026-02-10T09:30:00Z",
                        "description": "UTC",
                    },
                },
            },
            "SurveyResponse": {
                "type": "object",
                "properties": {
                    "response_id": {"type": "integer"},
                    "enrollment_id": {"type": "integer"},
                    "survey_type": {
                        "type": "string",
                        "enum": ["wochenfeedback", "abschlussfeedback"],
                    },
                    "survey_week": {
                        "type": "integer",
                        "nullable": True,
                        "description": "Nur beim Wochenfeedback: Trainingswoche (1 = erste Woche)",
                    },
                    "question_key": {
                        "type": "string",
                        "description": (
                            "wochenfeedback: zufriedenheit_gesamt (1–5, 5 = sehr zufrieden), tempo (1–5, 1 = viel zu "
                            "langsam, 3 = passend, 5 = viel zu schnell), freitext. "
                            "abschlussfeedback: nps (0–10), zufriedenheit_coach (1–5), weiterempfehlung_grund (Text)"
                        ),
                    },
                    "answer_value": {
                        "type": "string",
                        "description": "Antwort immer als String",
                    },
                    "submitted_at": {
                        "type": "string",
                        "example": "2026-02-13T16:40:00Z",
                        "description": "UTC",
                    },
                },
            },
        },
    },
    "paths": {
        "/login": {
            "post": {
                "summary": "Token anfordern",
                "requestBody": {
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        },
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        },
                    }
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "example": {
                                    "access_token": "…",
                                    "token_type": "bearer",
                                    "expires_in": TOKEN_TTL_SECONDS,
                                }
                            }
                        },
                    },
                    "401": {"description": "Falsche Zugangsdaten"},
                },
            }
        },
        "/trainings": {
            "get": {
                "summary": "Kurskatalog inkl. Module (nicht paginiert)",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Training"
                                            },
                                        },
                                        "as_of": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/participants": {
            "get": {
                "summary": "Teilnehmende (CRM)",
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "modified_after",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Nur Datensätze mit modified_at >= Wert (ISO 8601, lokale Zeit; "
                        "mit Offset/Z wird umgerechnet).",
                    }
                ]
                + _pagination_params(),
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Participant"
                                            },
                                        },
                                        "pagination": {
                                            "$ref": "#/components/schemas/Pagination"
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/enrollments": {
            "get": {
                "summary": "Teilnahmen / Buchungen (CRM)",
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "modified_after",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "training_id",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "participant_id",
                        "in": "query",
                        "schema": {"type": "integer"},
                    },
                ]
                + _pagination_params(),
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Enrollment"
                                            },
                                        },
                                        "pagination": {
                                            "$ref": "#/components/schemas/Pagination"
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/progress-events": {
            "get": {
                "summary": "Lernfortschritts-Events (LXP)",
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "since",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "event_time >= Wert (UTC)",
                    },
                    {
                        "name": "until",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "event_time < Wert (UTC)",
                    },
                    {
                        "name": "enrollment_id",
                        "in": "query",
                        "schema": {"type": "integer"},
                    },
                ]
                + _pagination_params(),
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/ProgressEvent"
                                            },
                                        },
                                        "pagination": {
                                            "$ref": "#/components/schemas/Pagination"
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/survey-responses": {
            "get": {
                "summary": "Feedback-Antworten (LXP)",
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "since",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "submitted_at >= Wert (UTC)",
                    },
                    {
                        "name": "survey_type",
                        "in": "query",
                        "schema": {
                            "type": "string",
                            "enum": ["wochenfeedback", "abschlussfeedback"],
                        },
                    },
                    {
                        "name": "enrollment_id",
                        "in": "query",
                        "schema": {"type": "integer"},
                    },
                ]
                + _pagination_params(),
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/SurveyResponse"
                                            },
                                        },
                                        "pagination": {
                                            "$ref": "#/components/schemas/Pagination"
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    },
}

DOCS_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Stackfuel Learning-Data API</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>window.onload=()=>{SwaggerUIBundle({url:'/openapi.json',dom_id:'#swagger-ui',persistAuthorization:true});};</script>
</body></html>"""


# ----------------------------------------------------------------------------
# HTTP-Handler
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "StackfuelLearningDataAPI/1.0"

    def log_message(self, fmt, *args):  # kompakteres Log
        print(
            f"{self.log_date_time_string()}  {self.command} {self.path}  -> {args[1] if len(args) > 1 else ''}"
        )

    def _send(
        self,
        status: int,
        body,
        content_type="application/json; charset=utf-8",
        extra_headers=None,
    ):
        data = (
            body
            if isinstance(body, bytes)
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, detail: str, extra_headers=None):
        self._send(status, {"detail": detail}, extra_headers=extra_headers)

    def _check_auth(self):
        header = self.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            raise ApiError(
                401,
                "Nicht authentifiziert. Bitte Bearer-Token aus POST /login mitsenden.",
            )
        token = header.split(" ", 1)[1].strip()
        exp = _tokens.get(token)
        if exp is None:
            raise ApiError(401, "Ungültiger Token.")
        if exp < time.time():
            del _tokens[token]
            raise ApiError(401, "Token abgelaufen. Bitte neu einloggen.")

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        query = parse_qs(url.query)
        try:
            if path == "/":
                return self._send(
                    200,
                    {
                        "service": "Stackfuel Learning-Data API (Test)",
                        "docs": "/docs",
                        "openapi": "/openapi.json",
                        "login": "POST /login",
                        "endpoints": sorted(PROTECTED),
                    },
                )
            if path == "/openapi.json":
                return self._send(200, OPENAPI)
            if path == "/docs":
                return self._send(
                    200,
                    DOCS_HTML.encode("utf-8"),
                    content_type="text/html; charset=utf-8",
                )
            if path == "/health":
                return self._send(200, {"status": "ok"})
            if path in PROTECTED:
                self._check_auth()
                if FLAKY and _rng.random() < FLAKY_RATE:
                    raise ApiError(
                        503, "Service temporarily unavailable – bitte erneut versuchen."
                    )
                return self._send(200, PROTECTED[path](query))
            raise ApiError(404, f"Unbekannter Pfad {path}")
        except ApiError as err:
            headers = {"Retry-After": "1"} if err.status == 503 else None
            self._error(err.status, err.detail, headers)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path.rstrip("/") != "/login":
            return self._error(404, "Unbekannter Pfad")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        ctype = self.headers.get("Content-Type", "")
        username = password = None
        try:
            if "json" in ctype:
                body = json.loads(raw or "{}")
                username, password = body.get("username"), body.get("password")
            else:
                form = parse_qs(raw)
                username, password = single(form, "username"), single(form, "password")
        except Exception:
            return self._error(400, "Body konnte nicht gelesen werden")
        if username == USERNAME and password == PASSWORD:
            token = secrets.token_urlsafe(24)
            _tokens[token] = time.time() + TOKEN_TTL_SECONDS
            return self._send(
                200,
                {
                    "access_token": token,
                    "token_type": "bearer",
                    "expires_in": TOKEN_TTL_SECONDS,
                },
            )
        return self._error(401, "Falscher Benutzername oder falsches Passwort")


def main():
    ap = argparse.ArgumentParser(description="Stackfuel Learning-Data API (Test)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"Stackfuel Learning-Data API läuft auf http://{args.host}:{args.port}  (Docs: /docs)"
    )
    print(
        f"  Datensätze: {len(TRAININGS)} Trainings, {len(PARTICIPANTS)} Teilnehmende, {len(ENROLLMENTS)} "
        f"Teilnahmen, {len(PROGRESS_EVENTS)} Events, {len(SURVEY_RESPONSES)} Feedback-Antworten"
    )
    print(
        f"  Zufällige 503-Antworten: {'aktiv' if FLAKY else 'deaktiviert'} (API_FLAKY=0 zum Abschalten)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
