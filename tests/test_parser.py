"""Tests for the Aegis-Code parser module."""

from pathlib import Path
import textwrap

import pytest

from aegis_code.core.parser import parse_file, parse_directory, ParsedModule


@pytest.fixture
def sample_code(tmp_path: Path) -> Path:
    """Create a temporary Python file with various constructs."""
    code = textwrap.dedent("""\
        import sqlite3
        import asyncio

        conn = sqlite3.connect(":memory:")


        def sync_function(x: int, y: str = "hello") -> str:
            for i in range(x):
                if i > 10:
                    break
            return y * x


        async def async_db_function(user_id: int):
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            rows = cursor.fetchall()
            return rows


        class UserService:
            def __init__(self, db):
                self.db = db

            async def get_user(self, user_id: int):
                result = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                return result.fetchone()

            def update_user(self, user_id: int, name: str):
                while True:
                    try:
                        self.db.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
                        self.db.commit()
                        break
                    except Exception:
                        continue
    """)
    file_path = tmp_path / "sample.py"
    file_path.write_text(code)
    return file_path


def test_parse_file_returns_parsed_module(sample_code: Path):
    result = parse_file(sample_code)
    assert isinstance(result, ParsedModule)
    assert result.file_path == str(sample_code)


def test_parse_file_finds_all_functions(sample_code: Path):
    result = parse_file(sample_code)
    names = [f.name for f in result.functions]
    assert "sync_function" in names
    assert "async_db_function" in names
    assert "get_user" in names
    assert "update_user" in names
    assert "__init__" in names


def test_parse_file_detects_async(sample_code: Path):
    result = parse_file(sample_code)
    async_funcs = [f for f in result.functions if f.is_async]
    async_names = [f.name for f in async_funcs]
    assert "async_db_function" in async_names
    assert "get_user" in async_names
    assert "sync_function" not in async_names


def test_parse_file_detects_db_calls(sample_code: Path):
    result = parse_file(sample_code)
    db_funcs = [f for f in result.functions if f.has_db_calls]
    assert len(db_funcs) >= 2  # async_db_function, get_user, update_user


def test_parse_file_detects_loops(sample_code: Path):
    result = parse_file(sample_code)
    sync_fn = next(f for f in result.functions if f.name == "sync_function")
    assert len(sync_fn.loops) >= 1
    assert sync_fn.loops[0].kind == "for"
    assert sync_fn.loops[0].has_break is True


def test_parse_file_detects_db_imports(sample_code: Path):
    result = parse_file(sample_code)
    assert result.has_db_imports is True


def test_parse_file_extracts_parameters(sample_code: Path):
    result = parse_file(sample_code)
    sync_fn = next(f for f in result.functions if f.name == "sync_function")
    assert "x" in sync_fn.parameters
    assert "y" in sync_fn.parameters


def test_parse_file_detects_classes(sample_code: Path):
    result = parse_file(sample_code)
    assert "UserService" in result.classes


def test_parse_file_handles_syntax_error(tmp_path: Path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def foo(:\n    pass\n")
    result = parse_file(bad_file)
    assert isinstance(result, ParsedModule)
    assert len(result.functions) == 0


def test_parse_directory(tmp_path: Path):
    (tmp_path / "mod_a.py").write_text("def hello(): pass\n")
    (tmp_path / "mod_b.py").write_text("async def world(): pass\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.py").write_text("x = 1\n")

    results = parse_directory(tmp_path)
    files = [m.file_path for m in results]
    assert any("mod_a.py" in f for f in files)
    assert any("mod_b.py" in f for f in files)
    # __pycache__ should be skipped
    assert not any("cache.py" in f for f in files)
