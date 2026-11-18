"""Tests for the Aegis-Code utility modules."""

import os
import sqlite3
from pathlib import Path

import pytest

from aegis_code.utils.helpers import (
    unified_diff,
    content_hash,
    truncate,
    resolve_llm_provider,
)
from aegis_code.utils.db import (
    get_connection,
    create_run,
    update_run,
    complete_run,
    record_failure,
    get_run,
    get_failures_for_run,
)


class TestHelpers:
    def test_unified_diff(self):
        original = "line1\nline2\nline3\n"
        patched = "line1\nline2_modified\nline3\n"
        diff = unified_diff(original, patched, "test.py")
        assert "---" in diff
        assert "+++" in diff
        assert "-line2" in diff
        assert "+line2_modified" in diff

    def test_unified_diff_no_changes(self):
        code = "line1\nline2\n"
        diff = unified_diff(code, code)
        assert diff == ""

    def test_content_hash(self):
        h1 = content_hash("hello")
        h2 = content_hash("hello")
        h3 = content_hash("world")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # SHA-256 hex digest

    def test_truncate_short(self):
        text = "short text"
        assert truncate(text, 100) == text

    def test_truncate_long(self):
        text = "x" * 1000
        result = truncate(text, 100)
        assert len(result) < 1000
        assert "truncated" in result

    def test_resolve_llm_provider_missing(self):
        env = os.environ.copy()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        os.environ.pop("AEGIS_LLM_PROVIDER", None)
        try:
            with pytest.raises(RuntimeError, match="No LLM API key"):
                resolve_llm_provider()
        finally:
            os.environ.update(env)


class TestDB:
    @pytest.fixture
    def db(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        yield conn
        conn.close()

    def test_create_and_get_run(self, db):
        run_id = create_run(db, "/some/path")
        assert run_id > 0
        run = get_run(db, run_id)
        assert run is not None
        assert run["target_path"] == "/some/path"
        assert run["status"] == "running"

    def test_update_run(self, db):
        run_id = create_run(db, "/path")
        update_run(db, run_id, status="completed", total_failures=5)
        run = get_run(db, run_id)
        assert run["status"] == "completed"
        assert run["total_failures"] == 5

    def test_complete_run(self, db):
        run_id = create_run(db, "/path")
        complete_run(db, run_id, status="done", total_failures=2, patches_applied=1)
        run = get_run(db, run_id)
        assert run["status"] == "done"
        assert run["completed_at"] is not None

    def test_record_and_get_failures(self, db):
        run_id = create_run(db, "/path")
        record_failure(db, run_id, "test_foo", "RuntimeError", "traceback", "critical")
        record_failure(db, run_id, "test_bar", "TimeoutError", "tb2", "high")
        failures = get_failures_for_run(db, run_id)
        assert len(failures) == 2
        assert failures[0]["test_name"] == "test_foo"
        assert failures[1]["severity"] == "high"

    def test_get_nonexistent_run(self, db):
        run = get_run(db, 999)
        assert run is None
