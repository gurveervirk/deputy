from deproc.core.context import Context
from deproc.core.interfaces.parser.models import Entity, SymbolID
from deproc.plugins.python.resolver import PythonResolver
from deputy.database.sqlite.main import get_entity_by_id, get_entity_ids_by_fqn
from deputy.database.sqlite.serialization import record_to_entity
import sqlite3

class SqlitePythonResolver(PythonResolver):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _get_symbol(self, symbol_id: SymbolID, context: Context) -> Entity | None:
        record = get_entity_by_id(self._conn, symbol_id)
        if record:
            return record_to_entity(record)
        return None

    def get_ids_by_fqn(
        self,
        module_fqn: str,
        symbol_name: str,
        context: Context,
    ) -> set[SymbolID]:
        fqn = f"{module_fqn}.{symbol_name}"
        ids = get_entity_ids_by_fqn(self._conn, fqn)
        if ids:
            return ids
        return set()
