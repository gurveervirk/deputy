from deproc.core.context import Context
from deproc.plugins.python.symbol_table_builder.main import PythonSymbolTableBuilder
from deproc.plugins.python.symbol_table_builder.models import PythonSymbolTable
import sqlite3

class SqliteSymbolTableBuilder(PythonSymbolTableBuilder):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def build(self, context: Context) -> PythonSymbolTable:
        symbol_table = super().build(context)
        cursor = self._conn.cursor()
        for module_fqn in symbol_table.module_symbol_maps:
            cursor.execute("DELETE FROM symbol_table_entries WHERE module_fqn = ?", (module_fqn,))
        for module_fqn, symbol_map in symbol_table.module_symbol_maps.items():
            for symbol_name, entity_ids in symbol_map.items():
                for sort_order, entity_id in enumerate(entity_ids):
                    cursor.execute(
                        "INSERT OR REPLACE INTO symbol_table_entries (module_fqn, symbol_name, entity_id, sort_order) VALUES (?, ?, ?, ?)",
                        (module_fqn, symbol_name, entity_id, sort_order),
                    )
        self._conn.commit()
        return symbol_table
