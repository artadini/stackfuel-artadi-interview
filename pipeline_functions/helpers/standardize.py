"""Pure, side-effect free value normalizers used by the curated layer.

Every function returns a standardized value *next to* the untouched source value; nothing
here discards data. Unknown/invalid values yield ``None`` (or an explicit status) so callers
can flag them and keep the original in a ``*_raw`` column.

Locale: German conventions are used for dates (``DD.MM.YYYY``, ``MM/YYYY``) and numbers
(decimal comma) unless the API specification defines otherwise (ISO dates, JSON numbers).
"""

import math
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_WHITESPACE = re.compile(r"\s+")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# An Excel date cell is stored as ISO datetime text in the raw layer (``2026-04-13T00:00:00``).
_ISO_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DE_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$")
_MONTH_YEAR = re.compile(r"^(\d{1,2})/(\d{2}|\d{4})$")
_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_SCORE_OVER = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)$")

# Markers that mean "no value" (compared case-insensitively). None of them occurs in the
# current sources (only empty strings do); the list is deliberately conservative, e.g. "na" and
# "nan" are real first names and are *not* markers. Extend it when a source starts using one.
EMPTY_MARKERS = {"", "-", "--", "n/a", "null", "none", "k.a.", "k. a."}
PLAUSIBLE_YEARS = (1990, 2100)

FUNDING_TYPES = ("bildungsgutschein", "selbstzahler", "firmenkunde")
FEDERAL_STATES = (
    "Baden-Württemberg",
    "Bayern",
    "Berlin",
    "Brandenburg",
    "Bremen",
    "Hamburg",
    "Hessen",
    "Mecklenburg-Vorpommern",
    "Niedersachsen",
    "Nordrhein-Westfalen",
    "Rheinland-Pfalz",
    "Saarland",
    "Sachsen",
    "Sachsen-Anhalt",
    "Schleswig-Holstein",
    "Thüringen",
)
TRAINING_VARIANTS = ("vollzeit", "teilzeit", "berufsbegleitend")
EVENT_TYPES = (
    "module_started",
    "exercise_submitted",
    "quiz_passed",
    "module_completed",
)
ENROLLMENT_STATUSES = (
    "angemeldet",
    "aktiv",
    "pausiert",
    "abgeschlossen",
    "abgebrochen",
    "storniert",
)
SURVEY_TYPES = ("wochenfeedback", "abschlussfeedback")
SURVEY_QUESTIONS = {
    "zufriedenheit_gesamt": "wochenfeedback",
    "tempo": "wochenfeedback",
    "freitext": "wochenfeedback",
    "nps": "abschlussfeedback",
    "zufriedenheit_coach": "abschlussfeedback",
    "weiterempfehlung_grund": "abschlussfeedback",
}
NUMERIC_SURVEY_RANGES = {
    "zufriedenheit_gesamt": (1, 5),
    "zufriedenheit_coach": (1, 5),
    "tempo": (1, 5),
    "nps": (0, 10),
}
OPEN_ENROLLMENT_STATUSES = {"angemeldet", "aktiv", "pausiert"}
UNKNOWN = "unknown"

BOOLEAN_TRUE = {"true", "1", "yes", "y", "ja", "j", "wahr"}
BOOLEAN_FALSE = {"false", "0", "no", "n", "nein", "falsch"}
AMPEL_VALUES = {
    "gruen": "green",
    "g": "green",
    "green": "green",
    "gelb": "yellow",
    "yellow": "yellow",
    "rot": "red",
    "r": "red",
    "red": "red",
}
# German particles that stay lower-case inside city names (Frankfurt am Main).
CITY_LOWERCASE_PARTICLES = {
    "am",
    "an",
    "auf",
    "bei",
    "der",
    "die",
    "das",
    "dem",
    "den",
    "im",
    "in",
    "ob",
    "unter",
    "vor",
    "von",
    "zu",
    "zum",
    "zur",
    "und",
    "a.",
    "d.",
}


# --------------------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------------------
def is_missing(value) -> bool:
    return value is None or str(value).strip().casefold() in EMPTY_MARKERS


def clean_text(value) -> str | None:
    """Trim and collapse internal whitespace; empty and marker values become ``None``."""
    if is_missing(value):
        return None
    return _WHITESPACE.sub(" ", str(value)).strip()


def trim_text(value) -> str | None:
    """Trim only (keeps internal whitespace; for free text). Empty values become ``None``."""
    if is_missing(value):
        return None
    return str(value).strip()


def transliterate(text: str) -> str:
    """German-aware ASCII form: ä->ae, ö->oe, ü->ue, ß->ss; other accents are stripped."""
    text = text.replace("ß", "ss").replace("ẞ", "SS")
    for source, target in (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("Ä", "Ae"),
        ("Ö", "Oe"),
        ("Ü", "Ue"),
    ):
        text = text.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def ascii_fold(value) -> str | None:
    """Case/diacritic-insensitive matching key (ä->ae, ß->ss) for names and similar text."""
    text = clean_text(value)
    if text is None:
        return None
    return transliterate(text.casefold())


def slugify(value) -> str | None:
    """snake_case category value: ``Agentur für Arbeit / Jobcenter`` -> ``agentur_fuer_arbeit_jobcenter``."""
    text = ascii_fold(value)
    if text is None:
        return None
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or None


def name_match_key(*parts) -> str | None:
    """Order-insensitive person key: folded name tokens, sorted.

    ``Müller, Ada`` and ``Ada Mueller`` both give ``ada mueller``; the workbook mixes
    ``Nachname, Vorname`` and ``Vorname Nachname``, so the order cannot be relied on.
    """
    tokens = []
    for part in parts:
        folded = ascii_fold(part)
        if folded:
            tokens.extend(re.findall(r"[a-z0-9]+", folded))
    return " ".join(sorted(tokens)) if tokens else None


def split_participant_name(value) -> tuple[str | None, str | None]:
    """Split a workbook name into (last, first). ``Nachname, Vorname`` splits at the comma;
    ``Vorname Nachname`` uses the last token as surname (compound surnames cannot be told
    apart, so the key above never depends on this split)."""
    text = clean_text(value)
    if text is None:
        return None, None
    if "," in text:
        last, first = text.split(",", 1)
        return clean_text(last), clean_text(first)
    parts = text.split(" ")
    if len(parts) == 1:
        return parts[0], None
    return parts[-1], " ".join(parts[:-1])


def _capitalize_component(component: str) -> str:
    """Upper-case the first letter; lower-case the rest only if the component is all upper or
    all lower (so ``McDonald`` and ``DeLuca`` keep their internal capitals)."""
    if not component:
        return component
    if component.isupper() or component.islower():
        return component[0].upper() + component[1:].lower()
    return component[0].upper() + component[1:]


def _capitalize_words(text: str, *, particles: set[str] | None = None) -> str:
    """Capitalize each space-separated word and each part after a hyphen or apostrophe."""
    words = text.split(" ")
    result = []
    for index, word in enumerate(words):
        if particles and index > 0 and word.casefold() in particles:
            result.append(word.casefold())
            continue
        pieces = re.split(r"([-'’])", word)
        result.append(
            "".join(
                piece if piece in {"-", "'", "’"} else _capitalize_component(piece)
                for piece in pieces
            )
        )
    return " ".join(result)


def normalize_person_name(value) -> str | None:
    """Every name component capitalized; hyphens and apostrophes kept."""
    text = clean_text(value)
    return None if text is None else _capitalize_words(text)


def normalize_city(value) -> str | None:
    """Capitalize every word and hyphen part; German particles (am, im, ...) stay lower-case."""
    text = clean_text(value)
    return (
        None
        if text is None
        else _capitalize_words(text, particles=CITY_LOWERCASE_PARTICLES)
    )


def normalize_email(value) -> str | None:
    text = trim_text(value)
    return text.casefold() if text else None


def is_valid_email(email: str | None) -> bool | None:
    if email is None:
        return None
    return bool(_EMAIL.match(email))


def normalize_upper(value) -> str | None:
    text = clean_text(value)
    return text.upper() if text else None


# --------------------------------------------------------------------------------------
# Categories (return (standardized value, status); empty -> "unknown", unmapped -> slug)
# --------------------------------------------------------------------------------------
def standardize_category(
    value, allowed: tuple[str, ...] | None = None
) -> tuple[str, str]:
    """Return ``(value, status)``; status is ``mapped``, ``missing`` or ``unmapped``.

    Empty values become ``unknown``. Values outside ``allowed`` stay readable (slug) but are
    reported as ``unmapped`` so the caller can quarantine them. ``allowed=None`` accepts any
    non-empty value.
    """
    slug = slugify(value)
    if slug is None:
        return UNKNOWN, "missing"
    if allowed is not None and slug not in allowed:
        return slug, "unmapped"
    return slug, "mapped"


def normalize_federal_state(value) -> tuple[str, str]:
    """Canonical German state name; empty -> ``unknown``; unknown names kept (cleaned)."""
    key = ascii_fold(value)
    if key is None:
        return UNKNOWN, "missing"
    for state in FEDERAL_STATES:
        if ascii_fold(state) == key:
            return state, "mapped"
    return clean_text(value), "unmapped"


def normalize_ampel(value) -> tuple[str, str]:
    """Map the many Ampel spellings; returns ``(green|yellow|red|unknown, mapped|missing|unmapped)``.

    ``g`` is read as grün and ``r`` as rot (the legend only defines grün/gelb/rot). Empty
    cells are ``unknown``: the workbook legend does not define a "not rated" state.
    """
    text = ascii_fold(value)
    if text is None:
        return UNKNOWN, "missing"
    key = re.sub(r"[^a-z]+", "", text)
    if key in AMPEL_VALUES:
        return AMPEL_VALUES[key], "mapped"
    return UNKNOWN, "unmapped"


def parse_boolean(value) -> tuple[bool | None, str]:
    """Return ``(bool | None, parsed|missing|invalid)``; empty is never interpreted as false."""
    if isinstance(value, bool):
        return value, "parsed"
    if is_missing(value):
        return None, "missing"
    key = str(value).strip().casefold()
    if key in BOOLEAN_TRUE:
        return True, "parsed"
    if key in BOOLEAN_FALSE:
        return False, "parsed"
    return None, "invalid"


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------
def _year(text: str) -> int:
    year = int(text)
    return year + 2000 if len(text) == 2 else year


def _build_date(year: int, month: int, day: int) -> date | None:
    if not PLAUSIBLE_YEARS[0] <= year <= PLAUSIBLE_YEARS[1]:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_iso_date(value) -> date | None:
    """Strict ``YYYY-MM-DD`` parser; anything else (including impossible dates) is ``None``."""
    text = clean_text(value)
    if text is None or not _ISO_DATE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parsed_or_invalid(parsed: date | None) -> tuple[date | None, str]:
    return (parsed, "parsed") if parsed else (None, "invalid")


def parse_flexible_date(value, *, month_year: bool = False) -> tuple[date | None, str]:
    """Parse Excel dates and German/ISO date strings; returns ``(date, parsed|missing|invalid)``.

    Accepts ``date``/``datetime`` cells, ``YYYY-MM-DD``, ``DD.MM.YYYY`` and ``DD.MM.YY``
    (day before month, two-digit years are 20xx). With ``month_year=True`` also ``MM/YYYY``
    and ``MM/YY``, which resolve to the first day of the month.
    """
    if isinstance(value, datetime):
        return value.date(), "parsed"
    if isinstance(value, date):
        return value, "parsed"
    if is_missing(value):
        return None, "missing"

    text = str(value).strip()
    datetime_match = _ISO_DATETIME.match(text)
    if datetime_match:  # an Excel date cell stored as text: keep only the date part
        text = datetime_match.group(1)

    if _ISO_DATE.match(text):
        return _parsed_or_invalid(parse_iso_date(text))
    german_date = _DE_DATE.match(text)
    if german_date:
        day, month, year = german_date.groups()
        return _parsed_or_invalid(_build_date(_year(year), int(month), int(day)))
    month_and_year = _MONTH_YEAR.match(text) if month_year else None
    if month_and_year:
        month, year = month_and_year.groups()
        return _parsed_or_invalid(_build_date(_year(year), int(month), 1))
    return None, "invalid"


def parse_cohort_date(value) -> tuple[date | None, str]:
    return parse_flexible_date(value, month_year=True)


# --------------------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------------------
def parse_decimal(value) -> tuple[Decimal | None, str]:
    """Parse numbers with German conventions; returns ``(Decimal, parsed|missing|invalid)``.

    JSON numbers are taken as they are. Strings: ``1.234,56`` (thousands dot, decimal comma),
    ``3,5``, ``12`` and ISO-style ``12.5`` are accepted. Invalid input is never turned into 0.
    """
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            return None, "invalid"
        return Decimal(str(value)), "parsed"
    if is_missing(value):
        return None, "missing"
    text = str(value).strip().replace(" ", "")
    try:
        if _THOUSANDS.match(text):
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        if not _NUMBER.match(text):
            return None, "invalid"
        return Decimal(text), "parsed"
    except InvalidOperation:
        return None, "invalid"


def parse_answer(value) -> tuple[Decimal | None, str, str]:
    """Split a survey answer into ``(score, context, kind)``.

    ``6/10`` -> (6, "10", "scored"); ``9`` -> (9, "unknown", "scored"); free text ->
    (None, "text", "text"); empty -> (None, "unknown", "missing"). The score is never
    derived from arbitrary text.
    """
    if is_missing(value):
        return None, "unknown", "missing"
    text = str(value).strip()
    match = _SCORE_OVER.match(text)
    if match:
        score, _ = parse_decimal(match.group(1))
        denominator, _ = parse_decimal(match.group(2))
        context = (
            str(int(denominator))
            if denominator == denominator.to_integral_value()
            else str(denominator)
        )
        return score, context, "scored"
    score, status = parse_decimal(text)
    if status == "parsed":
        return score, UNKNOWN, "scored"
    return None, "text", "text"


def nps_category(score) -> str | None:
    if score is None:
        return None
    if score >= 9:
        return "promoter"
    if score >= 7:
        return "passive"
    return "detractor"
