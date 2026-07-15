from deproc.core.context import Context
from deproc.plugins.python import (
    PythonSourceParser,
    PythonLinker,
)
from .database.sqlite import (
    SqliteSymbolCache,
    SqlitePythonResolver
)

def create_context(base_path: str, conn, enable_cache: bool = False) -> Context:
    ctx = Context(base_path=base_path)
    ctx.set_language("python", [".py", ".pyi"], aliases=["py"])
    ctx.set_parser("python", PythonSourceParser())
    ctx.set_resolver("python", SqlitePythonResolver(conn))
    ctx.set_linker("python", PythonLinker())
    ctx.set_skip_paths({
        "*.egg-info", "*.dist-info", "__pycache__", "node_modules", ".git",
        ".venv", ".mypy_cache", ".pytest_cache", "build", "dist",
    })
    if enable_cache:
        ctx.set_symbol_cache(SqliteSymbolCache(conn))
    return ctx
