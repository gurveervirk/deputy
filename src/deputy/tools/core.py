import os
from deputy._version import __version__
from deputy.database.sqlite import (
    clean_orphan_entities,
    delete_branch_entities,
    delete_branch_file,
    delete_dependency,
    delete_entities_by_package,
    get_branch_files,
    get_config,
    get_entities_by_ids,
    get_entities_by_path,
    get_entity_by_id,
    get_entity_by_path,
    init_schema,
    list_dependencies,
    open_database,
    search_entities as db_search_entities,
    set_config,
    update_mtime,
    upsert_branch_entities,
    upsert_branch_file,
    upsert_entity,
)
from deputy.utils.config_file import write_config, read_config
from deputy.utils.git import get_current_branch
from deputy.utils.storage import get_source_files
from deputy.core import create_context
from deputy.tools.utils import (
    _detect_file_changes,
    _is_stale,
    _open_database,
    _process_files,
)
from deputy.venv import (
    detect_venv,
    find_site_packages, 
    list_installed_packages,
    process_dependency
)

def init_database(path: str) -> None:
    conn = open_database(path)
    init_schema(conn)
    set_config(conn, "base_path", os.getcwd())
    set_config(conn, "deputy_cli_version", __version__)
    conn.commit()
    conn.close()
    if path != ".deputy.db":
        write_config("db_path", os.path.abspath(path))

    venv_path = detect_venv(os.getcwd())
    if venv_path:
        write_config("venv_path", venv_path)

def run_sync(force: bool, sync_deps: bool | None = None) -> None:
    conn = _open_database()
    branch = get_current_branch()
    base_path = get_config(conn, "base_path")
    if not base_path:
        base_path = os.getcwd()

    cfg = read_config()
    enable_cache = cfg.get("enable_cache", "false") == "true"
    ctx = create_context(base_path, conn, enable_cache=enable_cache)
    files = get_source_files(ctx)
    tracked = get_branch_files(conn, branch)

    file_hashes, changed, mtime_only, deleted = _detect_file_changes(files, tracked, base_path, force)

    dep_ids = _sync_deps_if_needed(conn, ctx, base_path, sync_deps, force)

    if not changed and not deleted and not mtime_only:
        if dep_ids:
            upsert_branch_entities(conn, branch, dep_ids)
            conn.commit()
        conn.close()
        return

    if not changed:
        for fmeta in files:
            if fmeta.path in mtime_only:
                update_mtime(conn, branch, fmeta.path, fmeta.mtime)
        if dep_ids:
            upsert_branch_entities(conn, branch, dep_ids)
        conn.commit()
        conn.close()
        return

    records, _ = _process_files(ctx, files, base_path)

    delete_branch_entities(conn, branch)

    for record in records:
        upsert_entity(conn, **record)

    upsert_branch_entities(conn, branch, [r["id"] for r in records])
    upsert_branch_entities(conn, branch, dep_ids)

    clean_orphan_entities(conn)

    for d in deleted:
        delete_branch_file(conn, branch, d)

    for fmeta in files:
        if fmeta.path in file_hashes:
            upsert_branch_file(conn, branch, fmeta.path, file_hashes[fmeta.path], fmeta.mtime)

    conn.commit()
    conn.close()

def _sync_deps_if_needed(conn, ctx, base_path, sync_deps_override, force) -> list[str]:
    if sync_deps_override is None:
        sync_deps = get_config(conn, "sync_deps") == "true"
    else:
        sync_deps = sync_deps_override
    if not sync_deps:
        return []

    file_config = read_config()
    venv_path = detect_venv(base_path, file_config)
    if not venv_path:
        return []

    site_packages = find_site_packages(venv_path)
    if not site_packages:
        return []

    max_files = int(file_config.get("max_dep_files", "5000"))
    packages = list_installed_packages(site_packages)
    tracked_deps = {p["package_name"] for p in list_dependencies(conn)}
    current_names = set()
    all_ids: list[str] = []
    for pkg in packages:
        current_names.add(pkg.name)
        ids = process_dependency(
            pkg.name,
            pkg.install_path,
            pkg.top_level_modules,
            pkg.version,
            ctx,
            conn,
            max_files,
        )
        all_ids.extend(ids)
    for name in tracked_deps - current_names:
        delete_entities_by_package(conn, name)
        delete_dependency(conn, name)

    return all_ids

def search_entities(pattern: str) -> list[dict]:
    conn = _open_database()
    branch = get_current_branch()

    cfg = read_config()
    if cfg.get("auto_sync", "false") == "true":
        base_path = get_config(conn, "base_path") or os.getcwd()
        try:
            if _is_stale(conn, branch, base_path):
                run_sync(force=False)
        except Exception:
            pass

    results = db_search_entities(conn, pattern, branch_name=branch)
    conn.close()
    return results

def get_entity_info(full_path: str, resolve: bool = False, all_matches: bool = False):
    conn = _open_database()
    branch = get_current_branch()

    cfg = read_config()
    if cfg.get("auto_sync", "false") == "true":
        base_path = get_config(conn, "base_path") or os.getcwd()
        try:
            if _is_stale(conn, branch, base_path):
                run_sync(force=False)
        except Exception:
            pass

    if resolve:
        base_path = get_config(conn, "base_path") or os.getcwd()
        cfg = read_config()
        enable_cache = cfg.get("enable_cache", "false") == "true"
        ctx = create_context(base_path, conn, enable_cache=enable_cache)
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
            entity = get_entity_by_path(conn, full_path, branch_name=branch)
        conn.close()
        return entity

    if all_matches:
        results = get_entities_by_path(conn, full_path, branch_name=branch)
        conn.close()
        return results if results else []

    entity = get_entity_by_path(conn, full_path, branch_name=branch)
    conn.close()
    return entity
