"""
tracegarden.core.fingerprint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
SQL query fingerprinting and N+1 detection.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List

from .models import DBQuery


# ---------------------------------------------------------------------------
# SQL normalisation patterns
# ---------------------------------------------------------------------------

# Single-quoted string literals: 'anything'
_RE_SINGLE_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'")

# Double-quoted string literals (used in some dialects as identifiers/values)
_RE_DOUBLE_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')

# Numeric literals (integers and floats, including negatives)
_RE_NUMBER = re.compile(r"\b-?\d+(?:\.\d+)?\b")

# IN (...) lists — already replaced to IN (?) by the numeric pass, but handle
# edge cases where several ?s remain: IN (?, ?, ?) → IN (?)
_RE_IN_LIST = re.compile(r"\bIN\s*\(\s*(?:\?,?\s*)+\)", re.IGNORECASE)

# Collapse multiple spaces/newlines/tabs into a single space
_RE_WHITESPACE = re.compile(r"\s+")

# Strip trailing semicolons
_RE_TRAILING_SEMI = re.compile(r";\s*$")


def fingerprint_sql(sql: str) -> str:
    """
    Return a normalised fingerprint for *sql* suitable for grouping.

    Transformations applied (in order):
    1. Strip leading/trailing whitespace.
    2. Replace single-quoted string literals with ``?``.
    3. Replace double-quoted string literals with ``?``.
    4. Replace numeric literals with ``?``.
    5. Collapse ``IN (?, ?, ...)`` to ``IN (?)``.
    6. Collapse all whitespace runs to a single space.
    7. Remove trailing semicolons.
    8. Uppercase keywords (best-effort: uppercase the whole fingerprint so
       ``select`` and ``SELECT`` group together).
    """
    if not sql:
        return ""

    fp = sql.strip()
    fp = _RE_SINGLE_QUOTED.sub("?", fp)
    fp = _RE_DOUBLE_QUOTED.sub("?", fp)
    fp = _RE_NUMBER.sub("?", fp)
    fp = _RE_IN_LIST.sub("IN (?)", fp)
    fp = _RE_WHITESPACE.sub(" ", fp)
    fp = _RE_TRAILING_SEMI.sub("", fp)
    fp = fp.upper()
    return fp.strip()


# ---------------------------------------------------------------------------
# N+1 detection
# ---------------------------------------------------------------------------

@dataclass
class NPlusOneWarning:
    """Describes a detected N+1 query pattern within a single request."""

    fingerprint: str
    count: int
    example_sql: str
    query_ids: List[str]

    def __str__(self) -> str:
        return (
            f"N+1 detected: '{self.fingerprint}' executed {self.count} times. "
            f"Example: {self.example_sql}"
        )


def detect_n_plus_one(
    queries: List[DBQuery], threshold: int = 5
) -> List[NPlusOneWarning]:
    """
    Scan *queries* for N+1 patterns.

    A pattern is flagged when the same fingerprint appears *threshold* or more
    times within the provided list (which should all belong to the same request).

    Returns a list of :class:`NPlusOneWarning` objects, one per flagged fingerprint.
    """
    # Group query IDs and collect example SQL by fingerprint
    groups: Dict[str, List[DBQuery]] = {}
    for q in queries:
        groups.setdefault(q.fingerprint, []).append(q)

    warnings: List[NPlusOneWarning] = []
    for fp, group in groups.items():
        if len(group) >= threshold:
            warnings.append(NPlusOneWarning(
                fingerprint=fp,
                count=len(group),
                example_sql=group[0].sql,
                query_ids=[q.id for q in group],
            ))

    # Sort by count descending so the worst offenders appear first
    warnings.sort(key=lambda w: w.count, reverse=True)
    return warnings


def annotate_duplicates(queries: List[DBQuery]) -> List[DBQuery]:
    """
    Mark queries with ``is_duplicate`` and ``duplicate_count`` based on
    fingerprint frequency within the provided list.

    Modifies the objects in-place and returns the same list.
    """
    counts: Counter = Counter(q.fingerprint for q in queries)
    for q in queries:
        c = counts[q.fingerprint]
        q.duplicate_count = c
        q.is_duplicate = c > 1
    return queries
