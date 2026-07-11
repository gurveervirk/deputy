import os
from pathlib import Path
from deputy._version import __version__
from deputy.database.sqlite import (
    delete_branch_file,
    delete_entity_by_module_fqn,
    get_branch_files,
    get_config,
    get_entities_by_ids,
    get_entities_by_path,
    get_entity_by_id,
    get_entity_by_path,
    init_schema,
    open_database,
    search_entities as db_search_entities,
    set_config,
    update_mtime,
    upsert_branch_file,
    upsert_entity,
)
from deputy.utils.git import get_current_branch
from deputy.utils.storage import compute_sha256, get_source_files
from deputy.core import create_context
from deputy.tools.utils import (
    _open_database,
    _build_module_exports,
    _entity_record,
)

def init_database(path: str) -> None:
    conn = open_database(path)
    init_schema(conn)
    set_config(conn, "base_path", os.getcwd())
    set_config(conn, "deputy_cli_version", __version__)
    conn.commit()
    conn.close()
    if path != ".deputy.db":
        Path(".deputyconfig").write_text(os.path.abspath(path))

def run_sync(force: bool) -> None:
    conn = _open_database()
    branch = get_current_branch()
    base_path = get_config(conn, "base_path")
    if not base_path:
        base_path = os.getcwd()

    ctx = create_context(base_path, conn)
    files = get_source_files(ctx)
    tracked = get_branch_files(conn, branch)

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

    if not changed and not deleted and not mtime_only:
        conn.close()
        return

    if not changed:
        for fmeta in files:
            if fmeta.path in mtime_only:
                update_mtime(conn, branch, fmeta.path, fmeta.mtime)
        conn.commit()
        conn.close()
        return

    parser = ctx.get_parser("python")
    source_files = []
    relpath_to_fqn: dict[str, str] = {}
    for fmeta in files:
        abs_path = os.path.join(base_path, fmeta.path)
        sf = parser.parse_file(abs_path, ctx)
        source_files.append(sf)
        relpath_to_fqn[fmeta.path] = sf.fqn

    linker = ctx.get_linker("python")
    linker.link_files(source_files, ctx)

    module_exports = _build_module_exports(ctx.entity_registry)

    for p in changed:
        if p in tracked:
            delete_entity_by_module_fqn(conn, relpath_to_fqn.get(p))

    for entity in list(ctx.entity_registry.values()):
        record = _entity_record(entity, ctx.entity_registry, module_exports)
        if record:
            upsert_entity(conn, **record)

    for d in deleted:
        delete_entity_by_module_fqn(conn, relpath_to_fqn.get(d))
        delete_branch_file(conn, branch, d)

    for fmeta in files:
        if fmeta.path in file_hashes:
            upsert_branch_file(conn, branch, fmeta.path, file_hashes[fmeta.path], fmeta.mtime)

    conn.commit()
    conn.close()

def search_entities(pattern: str) -> list[dict]:
    conn = _open_database()
    results = db_search_entities(conn, pattern)
    conn.close()
    return results

def get_entity_info(full_path: str, resolve: bool = False, all_matches: bool = False):
    conn = _open_database()

    if resolve:
        base_path = get_config(conn, "base_path") or os.getcwd()
        ctx = create_context(base_path, conn)
        parts = full_path.rsplit(".", 1)
        if len(parts) != 2:
            conn.close()
            return [] if all_matches else None
        module_fqn, symbol_name = parts
        resolver = ctx.get_resolver("python")
        result = resolver.resolve(module_fqn, symbol_name, ctx)
        if all_matches:
            entities = get_entities_by_ids(conn, result.resolved_ids)
            conn.close()
            return entities
        entity = None
        if result.resolved_ids:
            entity = get_entity_by_id(conn, next(iter(result.resolved_ids)))
        if not entity:
            entity = get_entity_by_path(conn, full_path)
        conn.close()
        return entity

    if all_matches:
        results = get_entities_by_path(conn, full_path)
        conn.close()
        return results if results else []

    entity = get_entity_by_path(conn, full_path)
    conn.close()
    return entity
