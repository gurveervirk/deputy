import os
from dataclasses import dataclass


@dataclass(frozen=True)
class FileMetadata:
    path: str
    mtime: float
    root_id: str = "project"
    root_kind: str = "project"
    root_path: str | None = None

    @property
    def relative_path(self) -> str:
        return self.path.replace(os.sep, "/")

    @property
    def logical_path(self) -> str:
        relative_path = self.relative_path
        if self.root_id == "project" and self.root_kind == "project":
            return relative_path
        return f"{self.root_kind}:{self.root_id}::{relative_path}"

    @property
    def absolute_path(self) -> str | None:
        if self.root_path is None:
            return None
        return os.path.abspath(os.path.join(self.root_path, self.path))
