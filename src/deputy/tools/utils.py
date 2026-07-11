import os
import sqlite3
from pathlib import Path
from deproc.plugins.python.linker.models import PythonModule
from deputy.database.sqlite import open_database
from deputy.database.sqlite.serialization import entity_to_record
from collections import defaultdict

_CONFIG_FILE = ".deputyconfig"
_DEFAULT_DB = ".deputy.db"

def _resolve_db_path() -> str:
    if os.path.exists(_CONFIG_FILE):
        path = Path(_CONFIG_FILE).read_text().strip()
        if path:
            return path
    if os.path.exists(_DEFAULT_DB):
        return _DEFAULT_DB
    raise FileNotFoundError(
        "No database found. Run 'deputy init' first."
    )

def _open_database() -> sqlite3.Connection:
    return open_database(_resolve_db_path())

def _build_module_exports(registry) -> dict[str, set[str]]:
    exports: dict[str, set[str]] = defaultdict(set)
    for entity in registry.values():
        if isinstance(entity, PythonModule) and hasattr(entity, "all_exports") and entity.all_exports:
            for name in entity.all_exports:
                exports[entity.fqn].add(name)
    return dict(exports)

def _entity_fqn(entity) -> str | None:
    fqn = getattr(entity, "fqn", None)
    if fqn:
        return fqn
    vb = getattr(entity, "variable_binding", None)
    if vb:
        return getattr(vb, "fqn", None)
    return None

def _entity_record(entity, registry, module_exports) -> dict | None:
    return entity_to_record(entity, module_exports=module_exports, registry=registry)
