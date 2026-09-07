from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from deproc.core.context import Context
from deproc.core.interfaces.resolver import ResolutionStatus
from deproc.plugins.java.parser.models import JavaCompilationUnit
from deproc.plugins.java.utils.serialization import (
    record_to_entity as java_record_to_entity,
)
from deproc.plugins.python.parser.models import PythonModule
from deproc.plugins.python.utils.serialization import (
    record_to_entity as python_record_to_entity,
)

from deputy.core import create_context
from deputy.database.sqlite import get_branch_entities


@dataclass(frozen=True)
class DeprocResolutionResult:
    language: str
    status: ResolutionStatus | None = None
    reason: str | None = None
    resolved: tuple[dict, ...] = ()
    unresolved: tuple[dict, ...] = ()
    inaccessible: tuple[dict, ...] = ()
    ambiguous: tuple[dict, ...] = ()


class DeprocResolutionAdapter:
    def __init__(self, conn: sqlite3.Connection, branch_name: str):
        self.conn = conn
        self.branch_name = branch_name
        self.records = {
            record["id"]: record for record in get_branch_entities(conn, branch_name)
        }
        self.context = create_context("", conn, enable_cache=False)
        self._load_entities()

    def _load_entities(self) -> None:
        for record in self.records.values():
            entity = self._record_to_entity(record)
            if entity is not None:
                self.context.entity_registry.add(entity)

    def _record_to_entity(self, record: dict):
        return _record_to_entity(record)

    def _infer_language(self, module_fqn: str) -> str | None:
        for entity_id in self.context.entity_registry.get_ids_by_fqn(module_fqn):
            entity = self.context.entity_registry.get(entity_id)
            if isinstance(entity, PythonModule):
                return "python"
            if isinstance(entity, JavaCompilationUnit):
                return "java"
        return None

    def _records(self, entity_ids: set[str]) -> tuple[dict, ...]:
        return tuple(
            sorted(
                (
                    self.records[entity_id]
                    for entity_id in entity_ids
                    if entity_id in self.records
                ),
                key=lambda record: (
                    record["full_path"],
                    record["type"],
                    record["id"],
                ),
            )
        )

    def resolve(
        self,
        module_fqn: str,
        symbol_name: str,
        language: str | None = None,
    ) -> DeprocResolutionResult:
        selected_language = language or self._infer_language(module_fqn)
        if selected_language not in {"python", "java"}:
            return DeprocResolutionResult(language=selected_language or "unknown")

        resolver = self.context.get_resolver(selected_language)
        if resolver is None:
            return DeprocResolutionResult(language=selected_language)

        result = resolver.resolve(module_fqn, symbol_name, self.context)
        status = getattr(result, "status", None) or (
            ResolutionStatus.RESOLVED
            if result.resolved_ids
            else ResolutionStatus.UNRESOLVED
        )
        assert isinstance(status, ResolutionStatus)
        return DeprocResolutionResult(
            language=selected_language,
            status=status,
            reason=getattr(result, "reason", None),
            resolved=self._records(result.resolved_ids),
            unresolved=self._records(result.unresolved_ids),
            inaccessible=self._records(getattr(result, "inaccessible_ids", set())),
            ambiguous=self._records(getattr(result, "ambiguous_ids", set())),
        )

    def resolve_type_reference(
        self,
        owner_entity_id: str,
        raw_name: str,
    ) -> DeprocResolutionResult:
        """Resolve a Java type reference (superclass/interface) using the deproc resolver."""
        resolver = self.context.get_resolver("java")
        resolve_type_reference = getattr(resolver, "resolve_type_reference", None)
        owner = self.context.entity_registry.get(owner_entity_id)
        if resolve_type_reference is None or owner is None:
            return DeprocResolutionResult(
                language="java", status=ResolutionStatus.UNRESOLVED
            )
        result = resolve_type_reference(raw_name, owner, self.context)
        return DeprocResolutionResult(
            language="java",
            status=result.status,
            reason=result.reason,
            resolved=self._records({result.value} if result.value else set()),
            ambiguous=self._records(set(result.candidates)),
        )


def load_context(conn: sqlite3.Connection, branch_name: str) -> Context:
    return DeprocResolutionAdapter(conn, branch_name).context


def _record_to_entity(record: dict):
    if record["language"] == "python":
        return python_record_to_entity(record)
    if record["language"] == "java":
        return java_record_to_entity(record)
    return None


def build_context_from_records(records: list[dict]) -> Context:
    """Rehydrate parsed records into a fresh deproc context (no DB dependency)."""
    ctx = create_context("", None, enable_cache=False)
    for record in records:
        entity = _record_to_entity(record)
        if entity is not None:
            ctx.entity_registry.add(entity)
    return ctx
