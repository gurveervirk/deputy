import os

from deproc.core.context import Context
from deproc.core.scope import AnalysisScope, RootDescriptor
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


def _configured_paths(base_path: str, value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(
        os.path.abspath(os.path.join(base_path, item))
        if not os.path.isabs(item)
        else os.path.abspath(item)
        for item in value.split(os.pathsep)
        if item.strip()
    )


def analysis_scope_from_config(
    base_path: str, config: dict[str, str] | None = None
) -> AnalysisScope:
    values = config or {}
    project_paths = _configured_paths(base_path, values.get("project_roots"))
    if "project_roots" not in values:
        project_paths = (os.path.abspath(base_path),)

    def roots(key: str, kind: str) -> tuple[RootDescriptor, ...]:
        return tuple(
            RootDescriptor(path, kind=kind, provenance="config")
            for path in _configured_paths(base_path, values.get(key))
        )

    def selected(key: str, separator: str = ",") -> list[str] | None:
        if key not in values:
            return None
        return [item.strip() for item in values[key].split(separator) if item.strip()]

    return AnalysisScope(
        project_roots=project_paths,
        source_roots=roots("source_roots", "source"),
        generated_roots=roots("generated_roots", "generated"),
        declaration_roots=roots("declaration_roots", "declaration"),
        dependency_roots=roots("dependency_roots", "dependency"),
        selected_languages=selected("analysis_languages"),
        selected_file_extensions=selected("analysis_extensions"),
        exclusions=selected("analysis_exclusions") or (),
    )


def create_context(
    base_path: str,
    conn,
    enable_cache: bool = False,
    scope: AnalysisScope | None = None,
) -> Context:
    ctx = Context(base_path=base_path, scope=scope)

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
