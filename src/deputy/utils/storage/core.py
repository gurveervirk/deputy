from deproc.core.context import Context
from deproc.core.discovery import find_source_files
from .models import FileMetadata
import hashlib
import os

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
    for filepath in find_source_files(context):
        rel_path = os.path.relpath(filepath, context.base_path)
        if any(part.startswith(".") for part in rel_path.split(os.sep)):
            continue
        try:
            stat = os.stat(filepath)
        except OSError:
            continue
        results.append(FileMetadata(path=rel_path, mtime=stat.st_mtime))
    return results
