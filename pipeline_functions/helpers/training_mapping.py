"""Map the many workbook spellings of a training to the catalog ``training_id`` (T01-T08).

The mapping is the explicit table in ``training_mapping.csv`` (same directory): one row per
*normalized spelling*. A row with exactly one ID is a ``matched`` spelling, a row with several
IDs separated by ``|`` is ``ambiguous`` (for example a track without a variant), and every
spelling that is not in the table is ``unmatched``. Nothing is guessed: add a row to the CSV
to teach the pipeline a new spelling.
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

from ..helpers.extraction_types import (
    TrainingMatch,
)

from .standardize import clean_text, transliterate

MAPPING_FILE = Path(__file__).with_name("training_mapping.csv")
VALID_TRAINING_ID = re.compile(r"^T0[1-8]$")


def normalize_training_spelling(value) -> str | None:
    """Casefold, transliterate umlauts, turn punctuation into spaces (``&`` becomes ``und``)."""
    text = clean_text(value)
    if text is None:
        return None
    text = transliterate(text.casefold()).replace("&", " und ")
    # "KI/ML" and "KI & ML" both mean the same: keep "und" only between full words.
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip() or None


@lru_cache(maxsize=1)
def load_training_mapping(path: Path = MAPPING_FILE) -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = normalize_training_spelling(row["normalized_spelling"])
            ids = tuple(
                part.strip() for part in row["training_ids"].split("|") if part.strip()
            )
            if not key or not ids or any(not VALID_TRAINING_ID.match(i) for i in ids):
                raise ValueError(f"Invalid training mapping row: {row}")
            if key in mapping and mapping[key] != ids:
                raise ValueError(f"Conflicting training mapping for {key!r}")
            mapping[key] = ids
    return mapping


def map_training(
    value, mapping: dict[str, tuple[str, ...]] | None = None
) -> TrainingMatch:
    """Return the match for one workbook ``Training`` cell."""
    mapping = mapping if mapping is not None else load_training_mapping()
    key = normalize_training_spelling(value)
    if key is None:
        return TrainingMatch(None, "missing", (), None)
    # "KI & ML" normalizes to "ki und ml"; the table stores the compact "ki ml" spelling too.
    ids = mapping.get(key) or mapping.get(re.sub(r"\b und \b", " ", f" {key} ").strip())
    if ids is None:
        return TrainingMatch(None, "unmatched", (), key)
    if len(ids) == 1:
        return TrainingMatch(ids[0], "matched", ids, key)
    return TrainingMatch(None, "ambiguous", ids, key)
