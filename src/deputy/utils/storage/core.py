import hashlib
import os

from deproc.core.context import Context
from deproc.core.discovery import discover_source_files

from deputy.logger import get_logger

from .models import FileMetadata

logger = get_logger("utils.storage")


def compute_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            block = f.read(65536)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def get_source_files(context: Context) -> list[FileMetadata]:
    results: list[FileMetadata] = []
    for discovered in discover_source_files(context):
        rel_path = discovered.relative_path
        if any(part.startswith(".") for part in rel_path.split("/")):
            continue
        try:
            stat = os.stat(discovered.path)
        except OSError:
            continue
        root_id = discovered.root_id
        if (
            discovered.root_kind == "project"
            and discovered.root.path == os.path.abspath(context.base_path)
        ):
            root_id = "project"
        results.append(
            FileMetadata(
                path=rel_path,
                mtime=stat.st_mtime,
                root_id=root_id,
                root_kind=discovered.root_kind,
                root_path=discovered.root.path,
            )
        )
    logger.debug("found %d source files", len(results))
    return results
