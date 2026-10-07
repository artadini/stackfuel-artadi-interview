# Stackfuel – Bewerbungsaufgabe Analytics Engineer

Dieses Repository enthält die Aufgabe und alle Materialien für die technische Aufgabe im Bewerbungsprozess.

**→ Die Aufgabenstellung findest du in [`AUFGABE.md`](AUFGABE.md).**

## Inhalt

```
.
├── AUFGABE.md                      # Aufgabenstellung – hier anfangen
├── api/
│   ├── server.py                   # lokale Test-API (CRM + LXP), nur Python-Standardbibliothek
│   ├── README.md                   # API-Dokumentation: Auth, Pagination, Filter, Datenmodell
│   ├── Dockerfile                  # optional: API im Container
│   └── data/                       # Rohdaten, die die API ausliefert (bitte nicht direkt einlesen*)
└── data/
    └── coach_betreuungsliste.xlsx  # manuell gepflegte Coach-Liste
```

\* Die CSV-Dateien unter `api/data/` sind die Datenbasis des Servers. Bitte nutze in deiner Pipeline die **API**, nicht die Dateien – der Umgang mit der API ist Teil der Aufgabe.

## Schnellstart

```bash
python api/server.py
# → http://127.0.0.1:8000/docs
```

Login: `stackfuel` / `learn-data-2026`

## Abgabe

Fork oder Kopie dieses Repos mit deiner Lösung (Link oder ZIP) – Details in `AUFGABE.md`, Abschnitt 6.
