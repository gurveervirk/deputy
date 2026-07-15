import os
from deputy._version import __version__
from deputy.database.sqlite import (
    delete_branch_file,
    delete_dependency,
    delete_entities_by_package,
    delete_entity_by_module_fqn,
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
    upsert_branch_file,
    upsert_entity,
)
from deputy.utils.config_file import write_config, read_config
from deputy.utils.git import get_current_branch
from deputy.utils.storage import get_source_files
from deputy.core import create_context
from deputy.tools.utils import (
    _detect_file_changes,
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

    _sync_deps_if_needed(conn, ctx, base_path, sync_deps, force)

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

    records, relpath_to_fqn = _process_files(ctx, files, base_path)

    for p in changed:
        if p in tracked:
            delete_entity_by_module_fqn(conn, relpath_to_fqn.get(p))

    for record in records:
        upsert_entity(conn, **record)

    for d in deleted:
        delete_entity_by_module_fqn(conn, relpath_to_fqn.get(d))
        delete_branch_file(conn, branch, d)

    for fmeta in files:
        if fmeta.path in file_hashes:
            upsert_branch_file(conn, branch, fmeta.path, file_hashes[fmeta.path], fmeta.mtime)

    conn.commit()
    conn.close()

def _sync_deps_if_needed(conn, ctx, base_path, sync_deps_override, force):
    if sync_deps_override is None:
        sync_deps = get_config(conn, "sync_deps") == "true"
    else:
        sync_deps = sync_deps_override
    if not sync_deps:
        return

    file_config = read_config()
    venv_path = detect_venv(base_path, file_config)
    if not venv_path:
        return

    site_packages = find_site_packages(venv_path)
    if not site_packages:
        return

    max_files = int(file_config.get("max_dep_files", "5000"))
    packages = list_installed_packages(site_packages)
    tracked_deps = {p["package_name"] for p in list_dependencies(conn)}
    current_names = set()
    for pkg in packages:
        current_names.add(pkg.name)
        process_dependency(
            pkg.name,
            pkg.install_path,
            pkg.top_level_modules,
            pkg.version,
            ctx,
            conn,
            max_files,
        )
    for name in tracked_deps - current_names:
        delete_entities_by_package(conn, name)
        delete_dependency(conn, name)

def search_entities(pattern: str) -> list[dict]:
    conn = _open_database()
    results = db_search_entities(conn, pattern)
    conn.close()
    return results

def get_entity_info(full_path: str, resolve: bool = False, all_matches: bool = False):
    conn = _open_database()

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
