import os
import json
import sqlite3
from pathlib import Path
from deproc.plugins.python.linker.models import PythonModule
from deputy.database.sqlite import open_database
from deputy.database.sqlite.serialization import entity_to_record
from deputy.utils.config_file import get_config as get_file_config
from deputy.utils.storage import compute_sha256
from collections import defaultdict

_CONFIG_FILE = ".deputyconfig"
_DEFAULT_DB = ".deputy.db"

def _resolve_db_path() -> str:
    cfg = get_file_config()
    db_path = cfg.get("db_path")
    if db_path and os.path.exists(db_path):
        return db_path
    if os.path.exists(_DEFAULT_DB):
        return _DEFAULT_DB
    raise FileNotFoundError(
        "No database found. Run 'deputy init' first."
    )

def _open_database() -> sqlite3.Connection:
    return open_database(_resolve_db_path())

def _detect_file_changes(
    files: list,
    tracked: dict[str, tuple[str, float]],
    base_path: str,
    force: bool,
) -> tuple[dict[str, str], set[str], set[str], set[str]]:
    file_hashes: dict[str, str] = {}
    changed: set[str] = set()
    mtime_only: set[str] = set()
    for fmeta in files:
        record = tracked.get(fmeta.path)
        if record is not None and record[1] == fmeta.mtime:
            continue
        abs_path = os.path.join(base_path, fmeta.path)
        h = compute_sha256(abs_path)
        file_hashes[fmeta.path] = h
        if record is None or record[0] != h or force:
            changed.add(fmeta.path)
        else:
            mtime_only.add(fmeta.path)
    
    deleted = set(tracked.keys()) - {f.path for f in files}
    return file_hashes, changed, mtime_only, deleted

def _build_module_exports(registry) -> dict[str, set[str]]:
    exports: dict[str, set[str]] = defaultdict(set)
    for entity in registry.values():
        if isinstance(entity, PythonModule) and hasattr(entity, "all_exports") and entity.all_exports:
            for name in entity.all_exports:
                exports[entity.fqn].add(name)
    return dict(exports)

def _process_files(
    ctx,
    files: list,
    base_path: str,
    entity_record_kwargs: dict | None = None,
) -> tuple[list[dict], dict[str, str]]:
    parser = ctx.get_parser("python")
    linker = ctx.get_linker("python")
    
    source_files = []
    relpath_to_fqn = {}
    for fmeta in files:
        abs_path = os.path.join(base_path, fmeta.path)
        sf = parser.parse_file(abs_path, ctx)
        source_files.append(sf)
        relpath_to_fqn[fmeta.path] = sf.fqn
    
    linker.link_files(source_files, ctx)

    module_exports = _build_module_exports(ctx.entity_registry)

    records = []
    kwargs_base = entity_record_kwargs or {}
    for entity in list(ctx.entity_registry.values()):
        kwargs = dict(kwargs_base)
        file_path = getattr(entity, "path", None)
        if file_path and file_path.endswith(".pyi"):
            kwargs["is_stub"] = True
        record = _entity_record(entity, ctx.entity_registry, module_exports, **kwargs)
        if record:
            records.append(record)
    
    return records, relpath_to_fqn

def _entity_fqn(entity) -> str | None:
    fqn = getattr(entity, "fqn", None)
    if fqn:
        return fqn
    vb = getattr(entity, "variable_binding", None)
    if vb:
        return getattr(vb, "fqn", None)
    return None

def _entity_record(entity, registry, module_exports, source="project", package_name=None, is_stub=False) -> dict | None:
    record = entity_to_record(entity, module_exports=module_exports, registry=registry)

    if record is None or source == "project":
        return record
    
    meta = json.loads(record["metadata_json"])
    meta["source"] = source

    if package_name:
        meta["package_name"] = package_name
    
    if is_stub:
        meta["is_stub"] = True
    
    record["metadata_json"] = json.dumps(meta, default=str)
    return record
