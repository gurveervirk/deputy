from __future__ import annotations

import json
from collections.abc import Set as AbstractSet

from deproc.core.interfaces.symbol_cache import SymbolCache


class SqliteSymbolCache(SymbolCache):
    language = "python"

    def __init__(self, conn):
        self.conn = conn
        self.cache = conn  # Dummy assignment, self.cache is not used here

    def get(self, module_fqn: str, symbol_name: str):
        row = self.conn.execute(
            "SELECT resolved_ids, unresolved_ids FROM cache_entries WHERE module_fqn = ? AND symbol_name = ?",
            (module_fqn, symbol_name),
        ).fetchone()
        if row is None:
            return None
        return set(json.loads(row["resolved_ids"])), set(
            json.loads(row["unresolved_ids"])
        )

    def set(
        self,
        module_fqn: str,
        symbol_name: str,
        resolved_ids: AbstractSet[str],
        unresolved_ids: AbstractSet[str],
    ):
        self.conn.execute(
            """INSERT OR REPLACE INTO cache_entries (module_fqn, symbol_name, resolved_ids, unresolved_ids)
               VALUES (?, ?, ?, ?)""",
            (
                module_fqn,
                symbol_name,
                json.dumps(list(resolved_ids)),
                json.dumps(list(unresolved_ids)),
            ),
        )
        self.conn.commit()

    def _link(self, module_fqn: str, key: tuple[str, str]):
        cache_module_fqn, cache_symbol_name = key
        self.conn.execute(
            """INSERT OR REPLACE INTO cache_module_links (module_fqn, cache_module_fqn, cache_symbol_name)
               VALUES (?, ?, ?)""",
            (module_fqn, cache_module_fqn, cache_symbol_name),
        )

    def add_cache_keys_for_module(
        self, module_fqn: str, keys: AbstractSet[tuple[str, str]]
    ):
        for key in keys:
            self._link(module_fqn, key)
        self.conn.commit()

    def get_cache_keys_for_module(self, module_fqn: str) -> set[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT cache_module_fqn, cache_symbol_name FROM cache_module_links WHERE module_fqn = ?",
            (module_fqn,),
        ).fetchall()
        return {(row["cache_module_fqn"], row["cache_symbol_name"]) for row in rows}

    def add_modules_for_cache_key(
        self, key: tuple[str, str], modules: AbstractSet[str]
    ):
        cache_module_fqn, cache_symbol_name = key
        for module_fqn in modules:
            self.conn.execute(
                """INSERT OR REPLACE INTO cache_module_links (module_fqn, cache_module_fqn, cache_symbol_name)
                   VALUES (?, ?, ?)""",
                (module_fqn, cache_module_fqn, cache_symbol_name),
            )
        self.conn.commit()

    def get_modules_for_cache_key(self, key: tuple[str, str]) -> set[str]:
        cache_module_fqn, cache_symbol_name = key
        rows = self.conn.execute(
            "SELECT module_fqn FROM cache_module_links WHERE cache_module_fqn = ? AND cache_symbol_name = ?",
            (cache_module_fqn, cache_symbol_name),
        ).fetchall()
        return {row["module_fqn"] for row in rows}

    def clear(self):
        self.conn.execute("DELETE FROM cache_entries")
        self.conn.execute("DELETE FROM cache_module_links")
        self.conn.commit()

    def clear_module(self, module_fqn: str):
        for cache_module_fqn, cache_symbol_name in self.get_cache_keys_for_module(
            module_fqn
        ):
            self.conn.execute(
                "DELETE FROM cache_entries WHERE module_fqn = ? AND symbol_name = ?",
                (cache_module_fqn, cache_symbol_name),
            )
        self.conn.execute(
            "DELETE FROM cache_module_links WHERE module_fqn = ?",
            (module_fqn,),
        )
        self.conn.commit()
