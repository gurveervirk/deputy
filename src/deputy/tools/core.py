import os
import json
from rich.console import Console
from deputy._version import __version__
from deputy.database.sqlite import (
    clean_orphan_entities,
    delete_branch_entities,
    delete_branch_file,
    delete_dependency,
    delete_entities_by_package,
    get_branch_files,
    get_config,
    get_dependency,
    get_entities_by_path,
    get_filtered_entities_by_path,
    get_dependency_entity_ids,
    get_entity_by_id,
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
from deputy.logger import get_logger
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
from deputy.tools.inheritance import (
    resolve_all_inherits,
    get_class_inheritance_info
)
from deputy.venv import (
    detect_venv,
    find_site_packages, 
    list_installed_packages,
    process_dependency
)

logger = get_logger("tools.core")
console = Console()

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
    if enable_cache:
        console.print("[dim]cache: enabled[/dim]")
    ctx = create_context(base_path, conn, enable_cache=enable_cache)
    files = get_source_files(ctx)
    tracked = get_branch_files(conn, branch)

    logger.info("sync started — branch=%s, base=%s, files=%d, force=%s", branch, base_path, len(files), force)
    logger.debug("tracked files: %d", len(tracked))

    file_hashes, changed, mtime_only, deleted = _detect_file_changes(files, tracked, base_path, force)

    logger.debug("changes — new/modified=%d, mtime_only=%d, deleted=%d", len(changed), len(mtime_only), len(deleted))

    dep_ids = _sync_deps_if_needed(conn, ctx, base_path, sync_deps, force, branch)

    if not changed and not deleted and not mtime_only:
        logger.info("sync complete — no changes")
        conn.close()
        return

    if not changed:
        for fmeta in files:
            if fmeta.path in mtime_only:
                update_mtime(conn, branch, fmeta.path, fmeta.mtime)
        conn.commit()
        conn.close()
        logger.info("sync complete — mtime-only update for %d files", len(mtime_only))
        return

    records, _ = _process_files(ctx, files, base_path)

    delete_branch_entities(conn, branch)

    for record in records:
        upsert_entity(conn, **record)

    resolve_all_inherits(conn, records)

    for record in records:
        if record["type"] == "CLASS":
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
    logger.info("sync complete — %d entities upserted, %d files processed", len(records), len(changed))

def _sync_deps_if_needed(conn, ctx, base_path, sync_deps_override, force, branch) -> list[str]:
    if sync_deps_override is None:
        sync_deps = get_config(conn, "sync_deps") == "true"
    else:
        sync_deps = sync_deps_override
    if not sync_deps:
        return []

    console.print("[dim]sync_deps: enabled, syncing packages...[/dim]")
    logger.info("dependency sync started")

    file_config = read_config()
    venv_path = detect_venv(base_path, file_config)
    if not venv_path:
        logger.warning("sync_deps enabled but no venv found")
        return []

    site_packages = find_site_packages(venv_path)
    if not site_packages:
        logger.warning("sync_deps enabled but site-packages not found in %s", venv_path)
        return []

    max_files = int(file_config.get("max_dep_files", "5000"))
    packages = list_installed_packages(site_packages)
    logger.info("dependency sync — %d packages found in %s", len(packages), site_packages)
    tracked_deps = {p["package_name"] for p in list_dependencies(conn)}
    current_names = set()
    all_ids: list[str] = []
    for pkg in packages:
        current_names.add(pkg.name)
        logger.debug("processing dependency: %s@%s (%d modules)", pkg.name, pkg.version, len(pkg.top_level_modules))
        tracked = get_dependency(conn, pkg.name)
        if (
            tracked
            and tracked["version"] == pkg.version
            and tracked.get("last_modified") == pkg.mtime
        ):
            existing = get_dependency_entity_ids(conn, branch, pkg.name)
            all_ids.extend(existing)
            continue
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
        logger.info("removing stale dependency: %s", name)
        delete_entities_by_package(conn, name)
        delete_dependency(conn, name)

    logger.info("dependency sync complete — %d packages synced", len(packages))
    return all_ids

def search_entities(
    pattern: str,
    type_filter: list[str] | None = None,
    language: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    exact: bool = False,
    name_only: bool = False,
) -> list[dict]:
    conn = _open_database()
    branch = get_current_branch()

    cfg = read_config()
    if cfg.get("auto_sync", "false") == "true":
        console.print("[dim]auto_sync: checking for changes...[/dim]")
        base_path = get_config(conn, "base_path") or os.getcwd()
        try:
            if _is_stale(conn, branch, base_path):
                console.print("[dim]auto_sync: project changed, running sync...[/dim]")
                run_sync(force=False)
        except Exception:
            logger.warning("auto_sync check failed, proceeding with existing data", exc_info=True)

    results = db_search_entities(
        conn, pattern, branch_name=branch,
        type_filter=type_filter, language=language,
        limit=limit, offset=offset,
        exact=exact, name_only=name_only,
    )
    logger.debug("search for %q returned %d results", pattern, len(results))
    conn.close()
    return results

def _compute_source(entity: dict, conn) -> str:
    meta = json.loads(entity["metadata_json"])
    if entity["type"] in ("MODULE", "PACKAGE", "NAMESPACE_PACKAGE"):
        return meta.get("path", "")
    lineno = meta.get("lineno", "")
    if not lineno:
        return ""
    sid = meta.get("source_id")
    if sid:
        src = get_entity_by_id(conn, sid)
        if src:
            src_meta = json.loads(src["metadata_json"])
            path = src_meta.get("path", "")
            if path:
                return f"{path}:{lineno}"
    return ""

def _get_source_file_path(entity: dict, conn, base_path: str) -> str | None:
    meta = json.loads(entity["metadata_json"])
    if entity["type"] in ("MODULE", "PACKAGE", "NAMESPACE_PACKAGE"):
        path = meta.get("path", "")
    else:
        sid = meta.get("source_id")
        if sid:
            src = get_entity_by_id(conn, sid)
            if src:
                path = json.loads(src["metadata_json"]).get("path", "")
            else:
                path = ""
        else:
            path = ""
    if not path:
        return None
    full = os.path.join(base_path, path) if not os.path.isabs(path) else path
    return full if os.path.isfile(full) else None

def _extract_source_text(entity: dict, conn, base_path: str) -> dict[str, str]:
    meta = json.loads(entity["metadata_json"])
    file_path = _get_source_file_path(entity, conn, base_path)
    if not file_path:
        return {}
    try:
        with open(file_path) as f:
            lines = f.readlines()
    except (FileNotFoundError, IOError):
        return {}
    result = {}
    for key in ("signature", "arguments", "return_type", "docstring"):
        lineno_key = f"{key}_lineno"
        end_lineno_key = f"{key}_end_lineno"
        if lineno_key in meta and end_lineno_key in meta:
            start = meta[lineno_key] - 1
            end = meta[end_lineno_key]
            text = "".join(lines[start:end]).strip()
            if text:
                result[key] = text
    return result

def get_entity_info(
    full_path: str,
    all_matches: bool = False,
    type_filter: str | None = None,
    lineno: int | None = None,
    extract: bool = False,
) -> dict | list[dict]:
    conn = _open_database()
    branch = get_current_branch()

    cfg = read_config()
    if cfg.get("auto_sync", "false") == "true":
        console.print("[dim]auto_sync: checking for changes...[/dim]")
        base_path = get_config(conn, "base_path") or os.getcwd()
        try:
            if _is_stale(conn, branch, base_path):
                console.print("[dim]auto_sync: project changed, running sync...[/dim]")
                run_sync(force=False)
        except Exception:
            logger.warning("auto_sync check failed, proceeding with existing data", exc_info=True)

    if type_filter or lineno is not None:
        results = get_filtered_entities_by_path(conn, full_path, branch_name=branch, type_filter=type_filter, lineno=lineno)
    else:
        results = get_entities_by_path(conn, full_path, branch_name=branch)

    if not results:
        conn.close()
        return [] if all_matches else None

    base_path = get_config(conn, "base_path") or os.getcwd()
    for row in results:
        row["_source"] = _compute_source(row, conn)
        if extract:
            row["_extracted"] = _extract_source_text(row, conn, base_path)
        if row["type"] == "CLASS":
            try:
                row["_inheritance_info"] = get_class_inheritance_info(conn, row["id"])
            except Exception:
                logger.debug("failed to compute inheritance info for %s", row["full_path"], exc_info=True)

    conn.close()

    if all_matches:
        return results

    result = results[0]
    result["_match_count"] = len(results)
    return result
