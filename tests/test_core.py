from unittest.mock import MagicMock

from deputy.core import create_context
from deputy.database.sqlite import SqliteSymbolCache


def test_create_context_sets_skip_paths():
    conn = MagicMock()
    ctx = create_context("/base", conn)
    assert ".venv" in ctx.skip_paths
    assert ".git" in ctx.skip_paths
    assert "__pycache__" in ctx.skip_paths


def test_create_context_cache_disabled_by_default():
    conn = MagicMock()
    ctx = create_context("/base", conn)
    assert ctx.get_symbol_cache("python") is None


def test_create_context_cache_disabled_explicitly():
    conn = MagicMock()
    ctx = create_context("/base", conn, enable_cache=False)
    assert ctx.get_symbol_cache("python") is None


def test_create_context_cache_enabled():
    conn = MagicMock()
    ctx = create_context("/base", conn, enable_cache=True)
    cache = ctx.get_symbol_cache("python")
    assert cache is not None
    assert isinstance(cache, SqliteSymbolCache)


def test_create_context_registers_java():
    conn = MagicMock()
    ctx = create_context("/base", conn)
    assert ctx.has_parser("java")
    assert ctx.has_linker("java")
    assert ".java" in ctx.selected_file_extensions


def test_create_context_registers_python():
    conn = MagicMock()
    ctx = create_context("/base", conn)
    assert ctx.has_parser("python")
    assert ctx.has_linker("python")
