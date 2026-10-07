# Stackfuel Learning-Data API (Test-Umgebung)

Lokaler HTTP-Server, der die Daten für die Aufgabe ausliefert. Er simuliert eine interne API, die zwei Quellsysteme zusammenführt:

| Quelle | Endpoints | Zeitstempel |
|---|---|---|
| CRM | `/participants`, `/enrollments` | lokale Zeit Europe/Berlin, **ohne** Offset (`2026-03-04T10:15:00`) |
| LXP (Lernplattform) | `/progress-events`, `/survey-responses` | UTC mit Suffix `Z` (`2026-03-04T09:15:00Z`) |
| Kurskatalog | `/trainings` | – |

## Starten

Voraussetzung: Python ≥ 3.9. Keine weiteren Pakete nötig.

```bash
cd api
python server.py            # läuft auf http://127.0.0.1:8000
python server.py --port 9000
```

Alternativ mit Docker:

```bash
docker build -t stackfuel-learning-api ./api
docker run --rm -p 8000:8000 stackfuel-learning-api
```

Interaktive Dokumentation (Swagger UI): <http://127.0.0.1:8000/docs>
OpenAPI-Spezifikation: <http://127.0.0.1:8000/openapi.json>

## Authentifizierung

```bash
curl -X POST http://127.0.0.1:8000/login \
     -d "username=stackfuel" -d "password=learn-data-2026"
# → {"access_token": "…", "token_type": "bearer", "expires_in": 3600}

curl http://127.0.0.1:8000/trainings -H "Authorization: Bearer <token>"
```

JSON-Body (`{"username": …, "password": …}`) funktioniert ebenfalls. Der Token ist 60 Minuten gültig.

## Pagination

Alle Listen-Endpoints außer `/trainings` liefern:

```json
{
  "data": [ … ],
  "pagination": {
    "page_size": 100,
    "returned": 100,
    "total_count": 658,
    "next_token": "MTAwOmE3YjJj…"
  }
}
```

Für die nächste Seite den Wert aus `next_token` als Query-Parameter `page_token` mitgeben. `next_token = null` heißt: letzte Seite. `page_size` darf maximal 500 sein. Ein `page_token` gilt nur für die Kombination aus Endpoint, Filtern und `page_size`, mit der er erzeugt wurde.

## Filter für inkrementelle Loads

| Endpoint | Parameter | Semantik |
|---|---|---|
| `/participants`, `/enrollments` | `modified_after` | `modified_at >= Wert` (lokale Zeit; mit Offset/`Z` wird umgerechnet) |
| `/progress-events` | `since`, `until` | `since <= event_time < until` (UTC) |
| `/survey-responses` | `since` | `submitted_at >= Wert` (UTC) |

Weitere Filter: `training_id`, `participant_id` (Enrollments), `enrollment_id` (Events, Feedback), `survey_type` (Feedback).

## Verhalten, das man kennen sollte

* Die API antwortet auf ca. 4 % der Anfragen mit `503 Service Unavailable` (Header `Retry-After: 1`). Ein robuster Client wiederholt die Anfrage. Zum Debuggen lässt sich das abschalten: `API_FLAKY=0 python server.py`.
* Abgelaufene oder fehlende Tokens liefern `401`.
* Fehlerhafte Parameter liefern `400`/`422` mit einer Erklärung im Feld `detail`.
* Die Daten sind ein fester Snapshot (Stand 01.09.2026). Der Server ändert nichts daran; ein Neustart stellt den Ausgangszustand wieder her.

## Datenmodell der Quelle (Kurzfassung)

* **Training**: `training_id`, `training_name`, `track`, `variant` (vollzeit / teilzeit / berufsbegleitend), `duration_weeks`, `list_price_eur`, `modules[]` (`module_id`, `module_order`, `module_name`, `estimated_hours`)
* **Participant**: `participant_id`, Name, `email`, `birth_date`, `city`, `federal_state`, `funding_type`, `acquisition_channel`, `created_at`, `modified_at`
* **Enrollment**: `enrollment_id`, `participant_id`, `training_id`, `cohort_start_date`, `planned_end_date`, `status` (angemeldet / aktiv / pausiert / abgeschlossen / abgebrochen / storniert), `status_changed_at`, `created_at`, `modified_at`
* **ProgressEvent**: `event_id`, `enrollment_id`, `module_id`, `event_type` (module_started / exercise_submitted / quiz_passed / module_completed), `event_time`
* **SurveyResponse**: `response_id`, `enrollment_id`, `survey_type` (wochenfeedback / abschlussfeedback), `survey_week`, `question_key`, `answer_value` (immer String), `submitted_at`

Fragen im Wochenfeedback: `zufriedenheit_gesamt` (1–5, 5 = sehr zufrieden), `tempo` (1–5, 1 = viel zu langsam, 3 = passend, 5 = viel zu schnell), `freitext`. Im Abschlussfeedback: `nps` (0–10), `zufriedenheit_coach` (1–5), `weiterempfehlung_grund` (Text).
