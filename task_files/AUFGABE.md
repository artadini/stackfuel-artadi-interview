# Bewerbungsaufgabe Analytics Engineer (m/w/d) – Stackfuel

Schön, dass du dabei bist! Diese Aufgabe gibt dir einen realistischen Eindruck davon, woran du bei uns arbeiten würdest, und uns einen Eindruck davon, wie du Datenprobleme angehst. Es geht **nicht** um Perfektion, sondern um nachvollziehbare Entscheidungen, sauberes Handwerk und klare Kommunikation.

**Zeitrahmen:** Plane etwa 4–6 Stunden ein. Du hast ab Erhalt **7 Kalendertage** Zeit für die Abgabe. Wenn du merkst, dass du deutlich länger brauchst, priorisiere und dokumentiere, was du weggelassen hast – das ist völlig in Ordnung.

---

## 1. Das Szenario

Stackfuel bildet Menschen in Data-Trainings weiter (Data Analyst, Data Scientist, Data Engineer, …) – in Vollzeit, Teilzeit und berufsbegleitend, oft gefördert über Bildungsgutscheine. Unsere Daten liegen verteilt:

* im **CRM** (Teilnehmende, Buchungen/Teilnahmen, Status),
* in der **Lernplattform (LXP)** (Lernfortschritt pro Modul, Feedback-Umfragen),
* in einer **Excel-Liste**, die unsere Coaches wöchentlich per Hand pflegen (wer betreut wen, Ampel-Status, letzter Kontakt).

Die Leitung *Training Operations* möchte ein wöchentliches KPI-Reporting: Wie viele Teilnehmende sind aktiv, wer kommt nicht voran, wie zufrieden sind Absolvent:innen, wie stark sind die Coaches ausgelastet? Heute werden diese Zahlen mühsam per Hand in Excel zusammengesucht – und jede Woche kommen andere Zahlen heraus.

**Deine Rolle:** Baue den ersten Wurf einer Datenpipeline, die diese Quellen zusammenführt, bereinigt, in ein analysefreundliches Modell überführt und die wichtigsten KPIs beantwortet.

> Die Daten sind synthetisch, enthalten aber – wie echte Systeme – Unsauberkeiten. Ein Teil der Aufgabe besteht darin, sie zu finden und bewusst damit umzugehen.

---

## 2. Was du bekommst

| Was | Wo | Hinweise |
|---|---|---|
| Lokale API mit CRM- und LXP-Daten | `api/` | Start: `python api/server.py` (nur Python-Standardbibliothek). Docs unter <http://127.0.0.1:8000/docs>. Details in [`api/README.md`](api/README.md). |
| Coach-Betreuungsliste | `data/coach_betreuungsliste.xlsx` | Manuell gepflegtes Excel, Stand 01.09.2026 |

Zugangsdaten für die API: Benutzername `stackfuel`, Passwort `learn-data-2026`.

Datenstand ist der **01.09.2026**. Alle Analysen beziehen sich auf den **Stichtag 31.08.2026**.

---

## 3. Deine Aufgabe (Pflichtteil)

### 3.1 Extract – Daten aus den Quellen holen

* Hole alle relevanten Daten aus der API (Trainings inkl. Module, Teilnehmende, Teilnahmen, Lernfortschritts-Events, Feedback) und aus der Excel-Liste.
* Die API ist paginiert, authentifiziert und nicht hundertprozentig zuverlässig – dein Client sollte damit umgehen.
* Überlege dir, wie ein **täglicher Lauf** aussähe: Wie lädst du nur neue oder geänderte Datensätze? Implementiere das, soweit es in der Zeit passt, oder beschreibe zumindest konkret, wie du es umsetzen würdest.

### 3.2 Transform – Bereinigen und modellieren

* Bereinige und normalisiere die Daten (Datentypen, Zeitzonen, Schreibweisen, Dubletten, Verweise ins Leere, …). Halte fest, **was** du gefunden und **wie** du entschieden hast.
* Führe die Coach-Liste mit den Systemdaten zusammen. Es gibt keinen gemeinsamen Schlüssel – du musst dir einen Weg überlegen und seine Grenzen benennen.
* Überführe die Daten in ein **analysefreundliches Modell** (z. B. Sternschema mit Fakten- und Dimensionstabellen), aus dem sich die Fragen in Abschnitt 3.4 einfach beantworten lassen. Ein kurzes ERD oder eine Skizze genügt zur Dokumentation.

### 3.3 Load – In eine Datensenke schreiben

* Lade das Modell in eine **lokal lauffähige** Datenbank deiner Wahl (z. B. DuckDB, SQLite, PostgreSQL via Docker). Begründe die Wahl kurz.
* Die Pipeline muss **reproduzierbar** sein: Mit einem Befehl (z. B. `make run`, `python pipeline.py`, `docker compose up`) entsteht aus den Quellen die befüllte Zieldatenbank. Ein zweiter Lauf darf keine Duplikate erzeugen.

### 3.4 Analyse – Die Fragen der Fachabteilung beantworten

Beantworte die folgenden Fragen per SQL (oder gleichwertig) auf deinem Modell und lege die Abfragen sowie die Ergebnisse (Tabelle oder Screenshot) bei. Wo eine Definition unklar ist, entscheide dich und dokumentiere die Entscheidung.

1. **Aktive Teilnehmende:** Wie viele Teilnahmen sind am Stichtag aktiv – je Training?
2. **Abschluss- und Abbruchquote:** Für Teilnahmen, deren geplantes Ende vor dem Stichtag liegt: Welcher Anteil ist abgeschlossen, welcher abgebrochen – je Training und Förderart (`funding_type`)?
3. **Lernfortschritt vs. Zeitfortschritt:** Für aktive Teilnahmen: Wie weit sind die Teilnehmenden im Curriculum (Anteil abgeschlossener Module) im Vergleich dazu, wie viel der geplanten Trainingsdauer bereits verstrichen ist? Welcher Anteil liegt mindestens 15 Prozentpunkte hinter dem Zeitplan („im Verzug")?
4. **NPS:** Wie hoch ist der Net Promoter Score (Promotoren 9–10 minus Detraktoren 0–6, in Prozent) aus dem Abschlussfeedback – je Training und Quartal?
5. **Coach-Auslastung:** Wie viele aktive oder pausierte Teilnahmen betreut jeder Coach laut Betreuungsliste, und wie viele davon stehen auf **rot**? Wie viele Teilnahmen konntest du keinem Coach zuordnen?

---

## 4. Bonus: Dashboard

Wenn du Zeit und Lust hast: Baue ein kleines Dashboard auf deiner Zieldatenbank, das die KPIs aus 3.4 für die Leitung *Training Operations* darstellt. Tool frei wählbar (Streamlit, Evidence, Metabase, Power BI, Tableau Public, ein Jupyter-Notebook mit Plots, …). Uns interessiert weniger die Optik als die Frage, ob die Darstellung einer Führungskraft hilft, Entscheidungen zu treffen – also z. B. Filter nach Training/Variante, sinnvolle Zeitachsen, Hervorhebung von Handlungsbedarf.

Der Bonus ist wirklich optional. Ein sauberer Pflichtteil ohne Dashboard ist uns lieber als ein Dashboard auf einer wackeligen Pipeline.

---

## 5. Rahmenbedingungen

**Tooling:** Frei wählbar. Python ist bei uns Standard, SQL sowieso; dbt, DuckDB, pandas/Polars, Airflow/Dagster/Prefect, Docker – alles willkommen, nichts Pflicht. Wähle, was du in der Zeit sicher beherrschst.

**KI-Unterstützung ist ausdrücklich erlaubt und erwünscht.** Wir nutzen im Alltag ChatGPT, Claude, Copilot & Co. und erwarten das auch von dir. Drei Bedingungen:

1. Du musst **jede Zeile** deiner Abgabe erklären und im Gespräch live verändern können.
2. Beschreibe in deiner README kurz und ehrlich, **wofür** du KI eingesetzt hast und wo sie dir *nicht* geholfen oder dich in die Irre geführt hat.
3. Die Verantwortung für Korrektheit liegt bei dir. Ein plausibel klingender KPI, der falsch ist, ist schlimmer als ein fehlender.

**Sprache:** Deutsch oder Englisch, wie es dir leichter fällt. Code und Kommentare gern auf Englisch.

---

## 6. Abgabe

Ein Git-Repository (Link auf GitHub/GitLab, öffentlich oder mit Zugang für uns) oder ein ZIP-Archiv per E-Mail. Enthalten sein sollen:

* **Code** der Pipeline (Extract, Transform, Load) und die **Abfragen** aus 3.4.
* **README** mit:
  * Setup und Startbefehl – wir müssen es auf einem frischen Rechner nachvollziehen können;
  * einer Skizze der Architektur und des Datenmodells (ERD, Bild oder Text);
  * deinen **Annahmen** und Definitionsentscheidungen;
  * einer Liste der **Datenqualitätsbefunde** und wie du jeweils damit umgegangen bist;
  * deiner **KI-Nutzung** (siehe oben);
  * einem Abschnitt **„Wenn das produktiv laufen sollte"**: Was würdest du anders machen, was fehlt (Tests, Monitoring, Orchestrierung, Datenschutz, …)?
* Die **Ergebnisse** der fünf Fragen (Tabellen oder Screenshots).
* Optional: Dashboard (Code, Link oder Screenshots).

Bitte lege **keine** befüllte Datenbank oder geladenen Rohdaten ins Repo – die Pipeline soll sie erzeugen.

---

## 7. Worauf wir achten

Wir bewerten transparent nach diesen Kriterien – in dieser Gewichtung:

| Kriterium | Was wir uns fragen |
|---|---|
| **Pipeline-Handwerk** | Läuft es reproduzierbar? Ist der API-Client robust (Auth, Pagination, Retries)? Ist ein wiederholter/inkrementeller Lauf mitgedacht? |
| **Datenmodellierung** | Ist das Zielmodell für die Fragen geeignet, verständlich benannt und erweiterbar? Sind Schlüssel und Beziehungen sauber? |
| **Umgang mit Datenqualität** | Hast du die Unsauberkeiten gefunden? Hast du bewusst entschieden und dokumentiert, statt still zu filtern oder zu ignorieren? |
| **Analytische Korrektheit** | Stimmen die KPIs im Rahmen deiner dokumentierten Definitionen? Sind die Definitionen fachlich sinnvoll? |
| **Kommunikation** | Können wir README und Code in 15 Minuten verstehen? Sind Trade-offs benannt? |
| **Bonus: Dashboard** | Hilft die Darstellung einer Führungskraft, Entscheidungen zu treffen? |

Nicht bewertet werden: Codezeilen-Anzahl, Framework-Vielfalt, Design-Feinschliff.

---

## 8. Das Folgegespräch (ca. 60 Minuten)

Du stellst deine Lösung in 10–15 Minuten vor (Bildschirm teilen, keine Folien nötig). Danach sprechen wir über deine Entscheidungen, schauen gemeinsam in den Code und verändern ihn an ein, zwei Stellen live – etwa eine neue Anforderung der Fachabteilung oder ein weiteres Datenproblem. Außerdem interessiert uns, wie du KI eingesetzt hast und wo du ihre Grenzen gesehen hast.

---

## Fragen?

Wenn etwas unklar ist oder technisch nicht funktioniert, melde dich jederzeit bei deiner Ansprechperson aus dem Recruiting. Rückfragen sind kein Minuspunkt – im Gegenteil.

Viel Spaß!
