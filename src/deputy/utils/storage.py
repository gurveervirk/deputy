import hashlib
import os
from dataclasses import dataclass
from deproc.core.context import Context
from deproc.core.discovery import find_source_files

@dataclass(frozen=True)
class FileChange:
    filepath: str
    content_hash: str
    last_modified: float

@dataclass(frozen=True)
class MtimeUpdate:
    filepath: str
    last_modified: float

def compute_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            block = f.read(65536)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()

def find_changed_files(
    context: Context,
    tracked_files: dict[str, tuple[str, float]],
) -> tuple[list[FileChange], list[MtimeUpdate]]:
    changed: list[FileChange] = []
    mtime_updates: list[MtimeUpdate] = []

    for filepath in find_source_files(context):
        rel_path = os.path.relpath(filepath, context.base_path)

        if any(part.startswith(".") for part in rel_path.split(os.sep)):
            continue

        try:
            stat = os.stat(filepath)
        except OSError:
            continue

        current_mtime = stat.st_mtime

        record = tracked_files.get(rel_path)
        if record is not None:
            existing_hash, existing_mtime = record

            if current_mtime == existing_mtime:
                continue

            current_hash = compute_sha256(filepath)

            if current_hash == existing_hash:
                mtime_updates.append(MtimeUpdate(rel_path, current_mtime))
                continue

            changed.append(FileChange(rel_path, current_hash, current_mtime))
        else:
            current_hash = compute_sha256(filepath)
            changed.append(FileChange(rel_path, current_hash, current_mtime))

    return changed, mtime_updates
