from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from deproc.core.context import Context
from deproc.core.interfaces.resolver import ResolutionStatus
from deproc.plugins.java.parser.models import JavaCompilationUnit
from deproc.plugins.java.utils.serialization import (
    record_to_entity as java_record_to_entity,
)
from deproc.plugins.python.parser.models import PythonClass, PythonModule
from deproc.plugins.python.resolver.models import (
    PythonClassMROResult,
    PythonInheritedMembersResult,
)
from deproc.plugins.python.utils.serialization import (
    record_to_entity as python_record_to_entity,
)

from deputy.core import create_context
from deputy.database.sqlite import get_branch_entities, get_inheritance_pin


@dataclass(frozen=True)
class DeprocResolutionResult:
    language: str
    status: ResolutionStatus | None = None
    reason: str | None = None
    resolved: tuple[dict, ...] = ()
    unresolved: tuple[dict, ...] = ()
    inaccessible: tuple[dict, ...] = ()
    ambiguous: tuple[dict, ...] = ()
    candidates: tuple[dict, ...] = ()


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

    def _result_from_ids(
        self,
        language: str,
        status: ResolutionStatus,
        reason: str | None,
        resolved_ids: set[str] | None = None,
        unresolved_ids: set[str] | None = None,
        inaccessible_ids: set[str] | None = None,
        ambiguous_ids: set[str] | None = None,
        candidate_ids: set[str] | None = None,
    ) -> DeprocResolutionResult:
        resolved_ids = resolved_ids or set()
        unresolved_ids = unresolved_ids or set()
        inaccessible_ids = inaccessible_ids or set()
        ambiguous_ids = ambiguous_ids or set()
        if candidate_ids is None:
            candidate_ids = set(ambiguous_ids)
            candidate_ids.update(inaccessible_ids)
            if status is ResolutionStatus.RESOLVED:
                candidate_ids.update(resolved_ids)
        return DeprocResolutionResult(
            language=language,
            status=status,
            reason=reason,
            resolved=self._records(resolved_ids),
            unresolved=self._records(unresolved_ids),
            inaccessible=self._records(inaccessible_ids),
            ambiguous=self._records(ambiguous_ids),
            candidates=self._records(candidate_ids),
        )

    def _adapt_resolver_result(self, language: str, result) -> DeprocResolutionResult:
        resolved_ids: set[str] = set(getattr(result, "resolved_ids", ()))
        unresolved_ids: set[str] = set(getattr(result, "unresolved_ids", ()))
        inaccessible_ids: set[str] = set(getattr(result, "inaccessible_ids", ()))
        ambiguous_ids: set[str] = set(getattr(result, "ambiguous_ids", ()))
        candidate_values = getattr(result, "candidates", None)
        candidate_ids = set(candidate_values) if candidate_values is not None else None
        status = getattr(result, "status", None)
        if not isinstance(status, ResolutionStatus):
            status = (
                ResolutionStatus.RESOLVED
                if resolved_ids
                else ResolutionStatus.UNRESOLVED
            )
        if status is ResolutionStatus.AMBIGUOUS and not ambiguous_ids:
            ambiguous_ids.update(candidate_ids or resolved_ids)
        if status is ResolutionStatus.INACCESSIBLE and not inaccessible_ids:
            inaccessible_ids.update(candidate_ids or set())
        value = getattr(result, "value", None)
        if status is ResolutionStatus.RESOLVED and value is not None:
            resolved_ids.add(value)
        return self._result_from_ids(
            language,
            status,
            getattr(result, "reason", None),
            resolved_ids,
            unresolved_ids,
            inaccessible_ids,
            ambiguous_ids,
            candidate_ids,
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
        return self._adapt_resolver_result(selected_language, result)

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
        candidate_ids = set(result.candidates)
        resolved_ids = (
            {result.value}
            if result.status is ResolutionStatus.RESOLVED and result.value is not None
            else set()
        )
        ambiguous_ids = (
            candidate_ids if result.status is ResolutionStatus.AMBIGUOUS else set()
        )
        inaccessible_ids = (
            candidate_ids if result.status is ResolutionStatus.INACCESSIBLE else set()
        )
        return self._result_from_ids(
            "java",
            result.status,
            result.reason,
            resolved_ids=resolved_ids,
            inaccessible_ids=inaccessible_ids,
            ambiguous_ids=ambiguous_ids,
            candidate_ids=candidate_ids,
        )

    def resolve_python_class_mro(
        self,
        class_entity_id: str,
        base_overrides: dict[tuple[str, str], str] | None = None,
    ) -> PythonClassMROResult:
        """Resolve a Python class MRO through deproc's semantic resolver."""
        resolver = self.context.get_resolver("python")
        resolve_class_mro = getattr(resolver, "resolve_class_mro", None)
        if resolve_class_mro is None:
            return PythonClassMROResult(
                status=ResolutionStatus.UNRESOLVED,
                reason="Python class-MRO resolution is unavailable",
            )
        if base_overrides is None:
            base_overrides = {}
            cls = self.context.entity_registry.get(class_entity_id)
            if isinstance(cls, PythonClass):
                for base_name in cls.inherits:
                    normalized_name = base_name.split("[", 1)[0].strip()
                    pin = get_inheritance_pin(
                        self.conn, class_entity_id, base_name, self.branch_name
                    )
                    if pin is None and normalized_name != base_name:
                        pin = get_inheritance_pin(
                            self.conn,
                            class_entity_id,
                            normalized_name,
                            self.branch_name,
                        )
                    if pin is not None:
                        base_overrides[(class_entity_id, normalized_name)] = pin[
                            "pinned_entity_id"
                        ]
        return resolve_class_mro(
            class_entity_id,
            self.context,
            base_overrides=base_overrides,
        )

    def resolve_python_import_alias(
        self, alias_entity_id: str
    ) -> DeprocResolutionResult:
        """Resolve one Python import binding through deproc's semantic resolver."""
        resolver = self.context.get_resolver("python")
        resolve_import_alias = getattr(resolver, "resolve_import_alias", None)
        if resolve_import_alias is None:
            return DeprocResolutionResult(
                language="python",
                status=ResolutionStatus.UNRESOLVED,
                reason="Python import-alias resolution is unavailable",
            )
        result = resolve_import_alias(alias_entity_id, self.context)
        return self._adapt_resolver_result("python", result)

    def get_python_inherited_members(
        self,
        class_entity_id: str,
        mro_result: PythonClassMROResult | None = None,
    ) -> PythonInheritedMembersResult:
        """Project inherited Python members from deproc's semantic MRO."""
        resolver = self.context.get_resolver("python")
        get_inherited_members = getattr(resolver, "get_inherited_members", None)
        if get_inherited_members is None:
            return PythonInheritedMembersResult(
                status=ResolutionStatus.UNRESOLVED,
                reason="Python inherited-member resolution is unavailable",
            )
        return get_inherited_members(
            class_entity_id,
            self.context,
            mro_result=mro_result,
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
