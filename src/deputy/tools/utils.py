import os
import json
import sqlite3
from dataclasses import dataclass, field
from deproc.plugins.python.linker.models import PythonModule
from deputy.database.sqlite import (
    open_database,
    get_branch_files,
    get_entity_ids_by_fqn,
    get_entity_by_id,
)
from deputy.database.sqlite.serialization import entity_to_record
from deputy.logger import get_logger
from deputy.utils.config_file import read_config
from deputy.utils.storage import compute_sha256, get_source_files
from deputy.core import create_context
from collections import defaultdict
from rich.tree import Tree

logger = get_logger("tools.utils")

def get_parent_id(entity: dict) -> str | None:
    pid = entity.get("parent_id")
    if pid:
        return pid
    meta = json.loads(entity["metadata_json"])
    return meta.get("parent_id")

def get_containing_module_fqn(conn: sqlite3.Connection, entity_id: str) -> str | None:
    seen: set[str] = set()
    current_id = entity_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        entity = get_entity_by_id(conn, current_id)
        if not entity:
            return None
        if entity["type"] in ("MODULE", "PACKAGE", "NAMESPACE_PACKAGE"):
            return entity["full_path"]
        current_id = get_parent_id(entity)
    return None

def module_is_package(conn: sqlite3.Connection, module_fqn: str) -> bool:
    ids = get_entity_ids_by_fqn(conn, module_fqn)
    for eid in ids:
        entity = get_entity_by_id(conn, eid)
        if entity and entity["type"] == "PACKAGE":
            return True
    return False

def resolve_relative_import(conn: sqlite3.Connection, import_stmt: dict, path: str) -> str | None:
    parent_id = get_parent_id(import_stmt)
    module_fqn = get_containing_module_fqn(conn, parent_id) if parent_id else None
    if not module_fqn:
        return None
    is_package = module_is_package(conn, module_fqn)
    relative_parts = path.split(".")
    parent_parts = module_fqn.split(".")
    num_leading_dots = len(path) - len(path.lstrip("."))
    levels_to_pop = num_leading_dots - (1 if is_package else 0)
    for _ in range(levels_to_pop):
        if parent_parts:
            parent_parts.pop()
    relative_parts = [p for p in relative_parts if p]
    return ".".join(parent_parts + relative_parts)

def resolve_import_alias(conn: sqlite3.Connection, alias: dict) -> tuple[str | None, str | None]:
    meta = json.loads(alias["metadata_json"])
    parent_id = get_parent_id(alias)
    import_name = meta.get("original_name", alias["name"])
    if not parent_id:
        return None, None
    import_stmt = get_entity_by_id(conn, parent_id)
    if not import_stmt:
        return None, None
    path = import_stmt["name"]
    if path and path.startswith("."):
        target_module = resolve_relative_import(conn, import_stmt, path)
    else:
        target_module = path
    if not target_module:
        return None, None
    return target_module, import_name

_DEFAULT_DB = ".deputy.db"

def _resolve_db_path() -> str:
    cfg = read_config()
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
        if record is not None and record[1] == fmeta.mtime and not force:
            continue
        abs_path = os.path.join(base_path, fmeta.path)
        h = compute_sha256(abs_path)
        file_hashes[fmeta.path] = h
        if record is None or record[0] != h or force:
            logger.debug("file changed: %s (hash mismatch or new)", fmeta.path)
            changed.add(fmeta.path)
        else:
            logger.debug("file mtime-only: %s", fmeta.path)
            mtime_only.add(fmeta.path)
    
    deleted = set(tracked.keys()) - {f.path for f in files}
    if deleted:
        logger.debug("files deleted: %s", ", ".join(sorted(deleted)))
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
        logger.debug("parsing: %s", fmeta.path)
        sf = parser.parse_file(abs_path, ctx)
        source_files.append(sf)
        relpath_to_fqn[fmeta.path] = sf.fqn
    
    logger.debug("linking %d source files", len(source_files))
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
    
    logger.debug("processed %d records from %d files", len(records), len(files))
    return records, relpath_to_fqn

def _entity_fqn(entity) -> str | None:
    fqn = getattr(entity, "fqn", None)
    if fqn:
        return fqn
    vb = getattr(entity, "variable_binding", None)
    if vb:
        return getattr(vb, "fqn", None)
    return None

def _is_stale(conn, branch, base_path):

    ctx = create_context(base_path, conn)
    files = get_source_files(ctx)
    tracked = get_branch_files(conn, branch)

    if not tracked:
        logger.debug("stale check: no tracked files, needs sync")
        return True

    current_paths = {f.path for f in files}
    tracked_paths = set(tracked.keys())

    if current_paths != tracked_paths:
        added = current_paths - tracked_paths
        removed = tracked_paths - current_paths
        logger.debug("stale check: files changed (added=%d, removed=%d)", len(added), len(removed))
        return True

    for fmeta in files:
        record = tracked.get(fmeta.path)
        if record is not None and record[1] != fmeta.mtime:
            logger.debug("stale check: mtime changed for %s", fmeta.path)
            return True

    logger.debug("stale check: up to date")
    return False

@dataclass
class _EntityTreeNode:
    label: str | None = None
    entities: list[str] = field(default_factory=list)
    children: dict[str, "_EntityTreeNode"] = field(default_factory=dict)

def build_entity_tree(results: list[dict], show_fqn: bool = False) -> Tree:
    root = _EntityTreeNode()

    seen: set[tuple[str, ...]] = set()
    for row in results:
        parts = row["full_path"].split(".")
        for i in range(len(parts)):
            seen.add(tuple(parts[:i]))
    for path_parts in seen:
        node = root
        for part in path_parts:
            if part not in node.children:
                node.children[part] = _EntityTreeNode()
            node = node.children[part]

    for row in results:
        parts = row["full_path"].split(".")
        label = f"[bold]{row['type']}[/bold] {row['name']}"
        if show_fqn:
            label += f" [dim]{row['full_path']}[/dim]"
        *parent_parts, last = parts
        node = root
        for part in parent_parts:
            node = node.children[part]
        if last in node.children and node.children[last].label is None:
            node.children[last].label = label
        else:
            node.entities.append(label)

    tree = Tree("Entities")
    _add_tree_node(tree, root)
    return tree

def _add_tree_node(parent: Tree, node: _EntityTreeNode) -> None:
    for label in node.entities:
        parent.add(label)
    for key, child in sorted(node.children.items()):
        branch_label = child.label if child.label else f"[dim]{key}[/dim]"
        branch = parent.add(branch_label)
        _add_tree_node(branch, child)

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
