from deproc.core.context import Context
from deproc.plugins.java import (
    JavaLinker,
    JavaResolver,
    JavaSourceParser,
)
from deproc.plugins.python import (
    PythonLinker,
    PythonResolver,
    PythonSourceParser,
)

from .database.sqlite import (
    SqliteSymbolCache,
)


def create_context(base_path: str, conn, enable_cache: bool = False) -> Context:
    ctx = Context(base_path=base_path)

    # Register python
    ctx.set_language("python", [".py", ".pyi"], aliases=["py"])
    ctx.set_parser("python", PythonSourceParser())
    ctx.set_linker("python", PythonLinker())
    ctx.set_resolver("python", PythonResolver())

    # Register java
    ctx.set_language("java", [".java"])
    ctx.set_parser("java", JavaSourceParser())
    ctx.set_linker("java", JavaLinker())
    ctx.set_resolver("java", JavaResolver())

    ctx.set_skip_paths(
        {
            "*.egg-info",
            "*.dist-info",
            "__pycache__",
            "node_modules",
            ".git",
            ".venv",
            ".mypy_cache",
            ".pytest_cache",
            "build",
            "dist",
        }
    )
    if enable_cache:
        ctx.set_symbol_cache(SqliteSymbolCache(conn))
    return ctx
