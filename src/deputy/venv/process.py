import os
from deproc.core.context import Context
from deproc.core.runtime import EntityRegistry
from deputy.database.sqlite import (
    delete_entities_by_package as db_delete_entities_by_package,
    upsert_entity,
    upsert_dependency,
)
from deputy.logger import get_logger
from deputy.utils.storage import get_source_files
from deputy.tools.utils import _process_files

logger = get_logger("venv.process")

def process_dependency(
    package_name: str,
    install_path: str,
    top_level_modules: list[str],
    version: str,
    template_ctx: Context,
    conn,
    max_files: int = 5000,
) -> list[str]:
    all_entities: list[dict] = []
    total_files = 0

    logger.debug("processing dependency %s@%s — %d top-level modules", package_name, version, len(top_level_modules))

    for module_name in top_level_modules:
        module_path = os.path.join(install_path, module_name)
        if not os.path.exists(module_path):
            continue

        ctx = Context(copy_from=template_ctx)
        ctx.base_path = install_path
        ctx.entity_registry = EntityRegistry()

        files = get_source_files(ctx)
        files = [f for f in files if f.path.startswith(module_name)]
        if not files:
            continue

        if total_files + len(files) > max_files:
            files = files[: max_files - total_files]
        total_files += len(files)

        records, _ = _process_files(ctx, files, install_path, entity_record_kwargs={
            "source": "dependency",
            "package_name": package_name,
        })
        all_entities.extend(records)

    db_delete_entities_by_package(conn, package_name)
    entity_ids: list[str] = []
    for record in all_entities:
        upsert_entity(conn, **record)
        entity_ids.append(record["id"])

    upsert_dependency(
        conn,
        package_name=package_name,
        version=version,
        install_path=install_path,
        package_path=install_path,
        source="venv",
        metadata_json=None,
        last_modified=os.path.getmtime(install_path) if os.path.exists(install_path) else None,
    )

    return entity_ids
