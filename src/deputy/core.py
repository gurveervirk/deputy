from deproc.core.context import Context
from deproc.plugins.python import (
    PythonSourceParser,
    PythonLinker,
)
from .database.sqlite import (
    SqliteSymbolCache,
    SqlitePythonResolver
)

def create_context(base_path: str, conn) -> Context:
    ctx = Context(base_path=base_path)
    ctx.set_language("python", [".py", ".pyi"], aliases=["py"])
    ctx.set_parser("python", PythonSourceParser())
    ctx.set_resolver("python", SqlitePythonResolver(conn))
    ctx.set_linker("python", PythonLinker())
    ctx.set_symbol_cache(SqliteSymbolCache(conn))
    return ctx
