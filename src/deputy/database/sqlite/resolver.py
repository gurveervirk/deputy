from deproc.core.context import Context
from deproc.core.interfaces.parser.models import SymbolID
from deproc.plugins.python.resolver import PythonResolver
from deputy.database.sqlite.main import get_entity_ids_by_fqn
import sqlite3

class SqlitePythonResolver(PythonResolver):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

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
        return context.entity_registry.get_ids_by_fqn(fqn)
