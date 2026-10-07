"""Row-level validation issues found while curating a record.

Severity decides the ``validation_status`` of the curated row (rows are never dropped):

* ``error``   - a record-level error (invalid date/timestamp/number/category, malformed survey
  value, invalid coach cell). The typed value is NULL, the original text stays in ``*_raw``
  and the row is ``quarantined`` (``is_quarantined``) so later layers can exclude it.
* ``review``  - the row is usable but needs attention (unmatched or ambiguous training or
  participant match) -> ``review``.
* ``warning`` - only an entry in ``quality_flags``.
"""

from ..helpers.extraction_types import (
    Issue,
)

ERROR = "error"
REVIEW = "review"
WARNING = "warning"


def warning(rule: str, message: str = "") -> Issue:
    return Issue(rule, WARNING, "warning", message or rule)


def error(rule: str, error_type: str, message: str) -> Issue:
    return Issue(rule, ERROR, error_type, message)


def review(rule: str, error_type: str, message: str) -> Issue:
    return Issue(rule, REVIEW, error_type, message)


def validation_status(issues: list[Issue]) -> str:
    severities = {issue.severity for issue in issues}
    if ERROR in severities:
        return "quarantined"
    if REVIEW in severities:
        return "review"
    return "valid"
