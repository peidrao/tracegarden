"""
Tests for tracegarden.core.fingerprint
"""
import pytest
from datetime import datetime, timezone

from tracegarden.core.fingerprint import fingerprint_sql, detect_n_plus_one, annotate_duplicates
from tracegarden.core.models import DBQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_query(sql: str, fp: str = None) -> DBQuery:
    """Create a DBQuery for testing with a given SQL and optional fingerprint override."""
    computed_fp = fp if fp is not None else fingerprint_sql(sql)
    return DBQuery(
        id="test-id",
        trace_id="abc123",
        span_id="span1",
        sql=sql,
        fingerprint=computed_fp,
        duration_ms=1.0,
        started_at=datetime.now(timezone.utc),
        parameters=[],
        db_vendor="sqlite",
        is_duplicate=False,
        duplicate_count=1,
    )


# ---------------------------------------------------------------------------
# fingerprint_sql — literal normalisation
# ---------------------------------------------------------------------------

class TestFingerprintSql:

    def test_single_quoted_string_normalised(self):
        sql = "SELECT * FROM users WHERE name = 'alice'"
        fp = fingerprint_sql(sql)
        assert fp == "SELECT * FROM USERS WHERE NAME = ?"
        assert "alice" not in fp

    def test_double_quoted_string_normalised(self):
        sql = 'SELECT * FROM users WHERE name = "alice"'
        fp = fingerprint_sql(sql)
        assert "alice" not in fp
        assert "?" in fp

    def test_integer_literal_normalised(self):
        sql = "SELECT * FROM orders WHERE id = 42"
        fp = fingerprint_sql(sql)
        assert "42" not in fp
        assert "?" in fp

    def test_float_literal_normalised(self):
        sql = "SELECT * FROM prices WHERE amount > 9.99"
        fp = fingerprint_sql(sql)
        assert "9.99" not in fp
        assert "?" in fp

    def test_multiple_literals_normalised(self):
        sql = "SELECT * FROM users WHERE id = 7 AND status = 'active' AND score > 3.5"
        fp = fingerprint_sql(sql)
        assert "7" not in fp
        assert "active" not in fp
        assert "3.5" not in fp
        assert fp.count("?") == 3

    def test_in_list_normalised_to_single_placeholder(self):
        sql = "SELECT * FROM products WHERE id IN (1, 2, 3, 4, 5)"
        fp = fingerprint_sql(sql)
        assert "IN (?)" in fp
        # Should not contain the expanded list
        assert "1, 2, 3" not in fp

    def test_in_list_with_strings_normalised(self):
        sql = "SELECT * FROM users WHERE status IN ('active', 'pending', 'banned')"
        fp = fingerprint_sql(sql)
        assert "IN (?)" in fp
        assert "active" not in fp

    def test_whitespace_collapsed(self):
        sql = "SELECT   *   FROM\n  users\n  WHERE\tid = 1"
        fp = fingerprint_sql(sql)
        assert "\n" not in fp
        assert "\t" not in fp
        assert "  " not in fp

    def test_trailing_semicolon_removed(self):
        sql = "SELECT * FROM users WHERE id = 1;"
        fp = fingerprint_sql(sql)
        assert not fp.endswith(";")

    def test_case_folded_to_uppercase(self):
        fp1 = fingerprint_sql("select * from users where id = 1")
        fp2 = fingerprint_sql("SELECT * FROM users WHERE id = 1")
        assert fp1 == fp2

    def test_empty_string(self):
        assert fingerprint_sql("") == ""

    def test_different_values_same_fingerprint(self):
        fp1 = fingerprint_sql("SELECT * FROM users WHERE id = 1")
        fp2 = fingerprint_sql("SELECT * FROM users WHERE id = 999")
        fp3 = fingerprint_sql("SELECT * FROM users WHERE id = 42")
        assert fp1 == fp2 == fp3

    def test_different_columns_different_fingerprint(self):
        fp1 = fingerprint_sql("SELECT * FROM users WHERE id = 1")
        fp2 = fingerprint_sql("SELECT * FROM users WHERE email = 'a@b.com'")
        # After normalisation both become `... WHERE ID = ?` and `... WHERE EMAIL = ?`
        assert fp1 != fp2

    def test_insert_literal_normalised(self):
        sql = "INSERT INTO logs (message, level) VALUES ('error occurred', 3)"
        fp = fingerprint_sql(sql)
        assert "error occurred" not in fp
        assert "3" not in fp
        assert fp.count("?") == 2


# ---------------------------------------------------------------------------
# detect_n_plus_one
# ---------------------------------------------------------------------------

class TestDetectNPlusOne:

    def test_six_identical_queries_triggers_warning(self):
        sql = "SELECT * FROM users WHERE id = 1"
        queries = [make_query(sql) for _ in range(6)]
        warnings = detect_n_plus_one(queries, threshold=5)
        assert len(warnings) == 1
        assert warnings[0].count == 6

    def test_exactly_threshold_triggers_warning(self):
        sql = "SELECT * FROM posts WHERE user_id = 1"
        queries = [make_query(sql) for _ in range(5)]
        warnings = detect_n_plus_one(queries, threshold=5)
        assert len(warnings) == 1
        assert warnings[0].count == 5

    def test_below_threshold_no_warning(self):
        sql = "SELECT * FROM comments WHERE id = 1"
        queries = [make_query(sql) for _ in range(4)]
        warnings = detect_n_plus_one(queries, threshold=5)
        assert len(warnings) == 0

    def test_five_different_queries_no_warning(self):
        """Five queries with completely different fingerprints should not trigger a warning."""
        sqls = [
            "SELECT * FROM users WHERE id = 1",
            "SELECT * FROM orders WHERE id = 2",
            "SELECT * FROM products WHERE id = 3",
            "SELECT * FROM categories WHERE id = 4",
            "SELECT * FROM reviews WHERE id = 5",
        ]
        queries = [make_query(sql) for sql in sqls]
        warnings = detect_n_plus_one(queries, threshold=5)
        assert len(warnings) == 0

    def test_multiple_n_plus_one_patterns_detected(self):
        """Two different patterns both above threshold are both flagged."""
        sql_a = "SELECT * FROM users WHERE id = 1"
        sql_b = "SELECT * FROM posts WHERE user_id = 1"
        queries = (
            [make_query(sql_a) for _ in range(7)] +
            [make_query(sql_b) for _ in range(5)]
        )
        warnings = detect_n_plus_one(queries, threshold=5)
        assert len(warnings) == 2
        # Worst offender first
        assert warnings[0].count >= warnings[1].count

    def test_warning_contains_example_sql(self):
        sql = "SELECT name FROM tags WHERE post_id = 42"
        queries = [make_query(sql) for _ in range(6)]
        warnings = detect_n_plus_one(queries, threshold=5)
        assert len(warnings) == 1
        assert warnings[0].example_sql == sql

    def test_custom_threshold(self):
        sql = "SELECT * FROM logs WHERE request_id = 1"
        queries = [make_query(sql) for _ in range(3)]
        # threshold=3 → exactly 3 queries with the same fingerprint should trigger
        warnings = detect_n_plus_one(queries, threshold=3)
        assert len(warnings) == 1
        # threshold=4 → should NOT trigger
        warnings_no = detect_n_plus_one(queries, threshold=4)
        assert len(warnings_no) == 0

    def test_empty_query_list(self):
        warnings = detect_n_plus_one([], threshold=5)
        assert warnings == []


# ---------------------------------------------------------------------------
# annotate_duplicates
# ---------------------------------------------------------------------------

class TestAnnotateDuplicates:

    def test_duplicate_flag_set_for_repeated_fingerprint(self):
        sql = "SELECT * FROM users WHERE id = 1"
        queries = [make_query(sql) for _ in range(3)]
        annotate_duplicates(queries)
        for q in queries:
            assert q.is_duplicate is True
            assert q.duplicate_count == 3

    def test_unique_queries_not_flagged(self):
        queries = [
            make_query("SELECT * FROM users WHERE id = 1"),
            make_query("SELECT * FROM posts WHERE id = 2"),
        ]
        annotate_duplicates(queries)
        for q in queries:
            assert q.is_duplicate is False
            assert q.duplicate_count == 1

    def test_mixed_queries(self):
        dup_sql = "SELECT * FROM users WHERE id = 1"
        unique_sql = "SELECT COUNT(*) FROM orders"
        queries = [make_query(dup_sql), make_query(dup_sql), make_query(unique_sql)]
        annotate_duplicates(queries)
        assert queries[0].is_duplicate is True
        assert queries[0].duplicate_count == 2
        assert queries[1].is_duplicate is True
        assert queries[2].is_duplicate is False
        assert queries[2].duplicate_count == 1
