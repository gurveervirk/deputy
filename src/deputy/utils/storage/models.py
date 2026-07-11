from dataclasses import dataclass

@dataclass(frozen=True)
class FileMetadata:
    path: str
    mtime: float
