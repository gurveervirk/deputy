import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from rich.console import Console
from rich.prompt import Prompt
from rich.tree import Tree

from deputy.database.sqlite import (
    get_entities_by_ids,
    get_entity_by_id,
    get_entity_ids_by_fqn,
)
from deputy.logger import get_logger
from deputy.tools.utils import (
    get_parent_id,
)
from deputy.tools.utils import (
    resolve_import_alias as shared_resolve_import_alias,
)

logger = get_logger("tools.resolve")


@dataclass
class ResolveStep:
    module_fqn: str
    symbol_name: str
    concrete: list[dict] = field(default_factory=list)
    aliases: list[dict] = field(default_factory=list)


class InteractiveResolver:
    def __init__(self, conn: sqlite3.Connection, mode: str = "default"):
        self.conn = conn
        self.mode = mode
        self.console = Console()

    def resolve(self, module_fqn: str, symbol_name: str) -> dict | None:
        steps: list[ResolveStep] = []
        current_module = module_fqn
        current_symbol = symbol_name

        while True:
            ids = get_entity_ids_by_fqn(self.conn, f"{current_module}.{current_symbol}")
            entities = get_entities_by_ids(self.conn, ids)

            if not entities:
                if steps:
                    self.console.print(
                        "[yellow]End of resolution chain — no further entities found[/yellow]"
                    )
                else:
                    self.console.print(
                        f"[red]Entity not found:[/red] {current_module}.{current_symbol}"
                    )
                return None

            concrete = [e for e in entities if e["type"] != "IMPORT_ALIAS"]
            aliases = [e for e in entities if e["type"] == "IMPORT_ALIAS"]

            step = ResolveStep(
                module_fqn=current_module,
                symbol_name=current_symbol,
                concrete=concrete,
                aliases=aliases,
            )
            steps.append(step)

            if not aliases and len(concrete) == 1:
                self._print_final_result(step, concrete[0])
                return concrete[0]

            if not aliases and len(concrete) > 1:
                self._print_step_header(step)
                return self._prompt_for_concrete(concrete)

            if len(aliases) == 1 and not concrete:
                if self.mode == "step":
                    self._print_step_header(step)
                    choice = self._prompt_for_choice(concrete, aliases)
                    if choice is None:
                        return None
                    if not choice["is_alias"]:
                        self._show_entity(choice["entity"])
                        return choice["entity"]
                    alias = choice["entity"]
                else:
                    alias = aliases[0]
                    peek = self._peek_target(alias)
                    self._print_auto_trace(step, alias, peek)
                next_module, next_symbol = shared_resolve_import_alias(self.conn, alias)
                if not next_module:
                    self.console.print("[red]Could not resolve import path[/red]")
                    return None
                assert next_symbol is not None
                current_module, current_symbol = next_module, next_symbol
                continue

            self._print_step_header(step)
            choice = self._prompt_for_choice(concrete, aliases)
            if choice is None:
                return None

            if not choice["is_alias"]:
                self._show_entity(choice["entity"])
                return choice["entity"]

            alias = choice["entity"]
            next_module, next_symbol = shared_resolve_import_alias(self.conn, alias)
            if not next_module:
                self.console.print("[red]Could not resolve import path[/red]")
                return None
            assert next_symbol is not None
            self.console.print()
            self.console.print(
                f"[dim]→ following import to {next_module}.{next_symbol}[/dim]"
            )
            current_module, current_symbol = next_module, next_symbol

    def _print_final_result(self, step: ResolveStep, entity: dict) -> None:
        self._print_step_header(step)
        meta = json.loads(entity["metadata_json"])
        sp = self._get_source_path(entity)
        lineno = meta.get("lineno", "")
        loc = f" @ {sp}:{lineno}" if sp and lineno else ""
        self.console.print(
            f"[green]→ resolved to: {entity['type']} {entity['full_path']}{loc}[/green]"
        )

    def _print_auto_trace(
        self, step: ResolveStep, alias: dict, peek: dict | None
    ) -> None:
        self.console.print()
        tree = Tree(f"[bold]{step.module_fqn}.{step.symbol_name}[/bold]")
        meta = json.loads(alias["metadata_json"])
        lineno = meta.get("lineno", "")
        sp = self._get_source_path(alias)
        loc = f" @ {sp}:{lineno}" if sp and lineno else ""
        alias_node = tree.add(f"IMPORT_ALIAS {alias['name']}{loc}")
        if peek:
            target_fqn = peek.get("target_fqn", "")
            target_desc = peek.get("display", "")
            target_loc = peek.get("loc", "")
            line = f"→ {target_fqn}"
            if target_desc:
                line += f"  ({target_desc})"
            if target_loc:
                line += f" @ {target_loc}"
            alias_node.add(line)
        self.console.print(tree)

    def _print_step_header(self, step: ResolveStep) -> None:
        self.console.print()
        tree = Tree(f"[bold]Step: {step.module_fqn}.{step.symbol_name}[/bold]")
        self.console.print(tree)

    def _show_entity(self, entity: dict) -> None:
        meta = json.loads(entity["metadata_json"])
        sp = self._get_source_path(entity)
        lineno = meta.get("lineno", "")
        loc = f" @ {sp}:{lineno}" if sp and lineno else ""
        self.console.print(
            f"  [green]→ {entity['type']} {entity['full_path']}{loc}[/green]"
        )

    def _prompt_for_choice(
        self,
        concrete: list[dict],
        aliases: list[dict],
    ) -> dict | None:
        tree, choices = self._build_choice_tree(concrete, aliases)
        self.console.print(tree)
        return self._prompt(choices)

    def _prompt_for_concrete(
        self,
        concrete: list[dict],
    ) -> dict | None:
        tree, choices = self._build_choice_tree(concrete, [])
        self.console.print(tree)
        return self._prompt(choices)

    def _prompt(self, choices: list[dict]) -> dict | None:
        if not choices:
            return None
        result = Prompt.ask(
            "Select an option (a - abort)",
            choices=[str(c["index"]) for c in choices] + ["a"],
            default="a",
            show_choices=False,
        )
        if result == "a":
            self.console.print("[yellow]Aborted.[/yellow]")
            return None
        chosen = choices[int(result) - 1]
        if not chosen["is_alias"]:
            self._show_entity(chosen["entity"])
        return chosen

    def _build_choice_tree(
        self, concrete: list[dict], aliases: list[dict]
    ) -> tuple[Tree, list[dict]]:
        choices: list[dict] = []
        idx = 1
        groups: dict[str, list[dict]] = defaultdict(list)

        for entity in concrete:
            group = self._entity_group(entity)
            c = {"index": idx, "entity": entity, "is_alias": False}
            groups[group].append(c)
            choices.append(c)
            idx += 1

        for entity in aliases:
            group = self._entity_group(entity)
            c = {"index": idx, "entity": entity, "is_alias": True}
            groups[group].append(c)
            choices.append(c)
            idx += 1

        tree = Tree("Choices:")
        for group_name in sorted(groups):
            branch = tree.add(f"[dim]{group_name}[/dim]")
            for c in groups[group_name]:
                if c["is_alias"]:
                    self._add_alias_tree_node(branch, c)
                else:
                    self._add_concrete_tree_node(branch, c)

        return tree, choices

    def _entity_group(self, entity: dict) -> str:
        meta = json.loads(entity["metadata_json"])
        path = meta.get("path", "")
        if path:
            return path
        sp = self._get_source_path(entity)
        if sp:
            return sp
        fp = entity["full_path"]
        parts = fp.rsplit(".", 1)
        return parts[0] if len(parts) > 1 else fp

    def _get_source_path(self, entity: dict) -> str | None:
        meta = json.loads(entity["metadata_json"])
        if entity["type"] in (
            "PYTHON_MODULE",
            "JAVA_MODULE",
            "PACKAGE",
            "NAMESPACE_PACKAGE",
            "COMPILATION_UNIT",
        ):
            return meta.get("path")
        sid = meta.get("source_id")
        if sid:
            src = get_entity_by_id(self.conn, sid)
            if src:
                src_meta = json.loads(src["metadata_json"])
                return src_meta.get("path")
        return None

    def _add_alias_tree_node(self, parent: Tree, choice: dict) -> None:
        entity = choice["entity"]
        meta = json.loads(entity["metadata_json"])
        original = meta.get("original_name", entity["name"])
        alias_str = meta.get("alias")
        display_name = (
            f"{original}"
            if not alias_str or alias_str == entity["name"]
            else f"{original} as {alias_str}"
        )
        lineno = meta.get("lineno", "")
        sp = self._get_source_path(entity)
        loc = f" @ {sp}:{lineno}" if sp and lineno else ""
        label = f"[{choice['index']}] [yellow]IMPORT_ALIAS[/yellow] {display_name}{loc}"
        node = parent.add(label)

        import_stmt = None
        parent_id = get_parent_id(entity)
        if parent_id:
            import_stmt = get_entity_by_id(self.conn, parent_id)
        if import_stmt:
            path = import_stmt["name"]
            node.add(f"from {path} import {original}")

        peek = self._peek_target(entity)
        if peek:
            target_fqn = peek.get("target_fqn", "")
            target_display = peek.get("display", "")
            target_loc = peek.get("loc", "")
            if target_fqn:
                peek_node = node.add(f"[dim]→ {target_fqn}[/dim]")
                child = ""
                if target_display:
                    child = target_display
                if target_loc:
                    child += f" @ {target_loc}" if child else target_loc
                if child:
                    peek_node.add(f"[dim]{child}[/dim]")

    def _add_concrete_tree_node(self, parent: Tree, choice: dict) -> None:
        entity = choice["entity"]
        meta = json.loads(entity["metadata_json"])
        lineno = meta.get("lineno", "")
        sp = self._get_source_path(entity)
        loc = f" @ {sp}:{lineno}" if sp and lineno else ""
        label = (
            f"[{choice['index']}] [green]{entity['type']}[/green] {entity['name']}{loc}"
        )
        parent.add(label)

    def _peek_target(self, alias: dict) -> dict | None:
        target_module, symbol_name = shared_resolve_import_alias(self.conn, alias)
        if not target_module:
            return None

        target_fqn = f"{target_module}.{symbol_name}"
        ids = get_entity_ids_by_fqn(self.conn, target_fqn)
        if not ids:
            return {"target_fqn": target_fqn, "display": None, "loc": ""}

        entities = get_entities_by_ids(self.conn, ids)
        concrete = [e for e in entities if e["type"] != "IMPORT_ALIAS"]

        if concrete:
            top = concrete[0]
            meta = json.loads(top["metadata_json"])
            display = f"{top['type']} {top['name']}"
            sp = self._get_source_path(top)
            ln = meta.get("lineno", "")
            loc = f"{sp}:{ln}" if sp and ln else ""
            return {"target_fqn": target_fqn, "display": display, "loc": loc}
        elif entities:
            top = entities[0]
            return {
                "target_fqn": target_fqn,
                "display": f"{top['type']} {top['name']}",
                "loc": "",
            }
        return {"target_fqn": target_fqn, "display": None, "loc": ""}

    def _format_concrete(self, entity: dict) -> str:
        meta = json.loads(entity["metadata_json"])
        path = meta.get("path", "")
        lineno = meta.get("lineno", "")
        loc = f"{path}:{lineno}" if path and lineno else entity["full_path"]
        return loc

    def _format_alias(self, entity: dict) -> str:
        meta = json.loads(entity["metadata_json"])
        original = meta.get("original_name", entity["name"])
        alias_str = meta.get("alias")
        display_name = (
            f"{original} as {alias_str}"
            if alias_str and alias_str != entity["name"]
            else original
        )
        parent_id = get_parent_id(entity)
        container_path = ""
        if parent_id:
            parent = get_entity_by_id(self.conn, parent_id)
            if parent:
                parent_meta = json.loads(parent["metadata_json"])
                container_path = parent_meta.get("path", "")
        return f"{display_name} in {entity['full_path']}" + (
            f" ({container_path})" if container_path else ""
        )

    def resolve_all(self, module_fqn: str, symbol_name: str) -> list[dict]:
        results: list[dict] = []
        self._collect_terminals(f"{module_fqn}.{symbol_name}", set(), results)
        return results

    def _collect_terminals(
        self, fqn: str, visited: set[str], results: list[dict]
    ) -> None:
        if fqn in visited:
            return
        visited.add(fqn)
        ids = get_entity_ids_by_fqn(self.conn, fqn)
        if not ids:
            return
        entities = get_entities_by_ids(self.conn, ids)
        if not entities:
            return
        concretes = [e for e in entities if e["type"] != "IMPORT_ALIAS"]
        aliases = [e for e in entities if e["type"] == "IMPORT_ALIAS"]
        results.extend(concretes)
        for alias in aliases:
            next_mod, next_sym = shared_resolve_import_alias(self.conn, alias)
            if next_mod:
                self._collect_terminals(f"{next_mod}.{next_sym}", visited, results)

    def _print_all_tree(self, module_fqn: str, symbol_name: str) -> None:
        fqn = f"{module_fqn}.{symbol_name}"
        visited: set[str] = set()
        resolved_targets: set[str] = set()
        tree = self._build_all_tree(fqn, visited, resolved_targets)
        if tree:
            self.console.print(tree)
        terminals = self.resolve_all(module_fqn, symbol_name)
        if terminals:
            self.console.print()
            self.console.print("[bold]Resolved leaves:[/bold]")
            for ent in terminals:
                meta = json.loads(ent["metadata_json"])
                sp = self._get_source_path(ent)
                lineno = meta.get("lineno", "")
                loc = f" @ {sp}:{lineno}" if sp and lineno else ""
                self.console.print(
                    f"  [green]→ {ent['type']} {ent['name']}{loc}[/green]"
                )
        elif not tree:
            self.console.print(f"\n[red]Entity not found:[/red] {fqn}")

    def _build_all_tree(
        self, fqn: str, visited: set[str], resolved_targets: set[str]
    ) -> Tree:
        root = Tree(f"[bold]{fqn}[/bold]")
        if fqn in visited:
            root.add("[dim](circular import)[/dim]")
            return root
        visited.add(fqn)
        ids = get_entity_ids_by_fqn(self.conn, fqn)
        entities = get_entities_by_ids(self.conn, ids) if ids else []
        if not entities:
            visited.discard(fqn)
            return root
        concretes = [e for e in entities if e["type"] != "IMPORT_ALIAS"]
        aliases = [e for e in entities if e["type"] == "IMPORT_ALIAS"]
        for ent in concretes:
            meta = json.loads(ent["metadata_json"])
            sp = self._get_source_path(ent)
            lineno = meta.get("lineno", "")
            loc = f" @ {sp}:{lineno}" if sp and lineno else ""
            root.add(f"[green]{ent['type']} {ent['name']}{loc}[/green]")
        if not aliases:
            visited.discard(fqn)
            return root
        for alias in aliases:
            meta = json.loads(alias["metadata_json"])
            lineno = meta.get("lineno", "")
            sp = self._get_source_path(alias)
            loc = f" @ {sp}:{lineno}" if sp and lineno else ""
            label = f"[yellow]IMPORT_ALIAS[/yellow] {alias['name']}{loc}"
            alias_node = root.add(label)
            parent_id = get_parent_id(alias)
            from_line = ""
            imp_name = ""
            original = alias["name"]
            if parent_id:
                imp = get_entity_by_id(self.conn, parent_id)
                if imp:
                    imp_name = imp["name"]
                    original = meta.get("original_name", alias["name"])
                    from_line = f"[dim]from {imp_name} import {original}[/dim]"
            next_mod, next_sym = shared_resolve_import_alias(self.conn, alias)
            if not next_mod:
                if from_line:
                    alias_node.add(from_line)
                continue
            target = f"{next_mod}.{next_sym}"
            if target in resolved_targets and from_line:
                alias_node.add(
                    f"[dim]from {imp_name} import {original} (skipped, duplicate path)[/dim]"
                )
                continue
            resolved_targets.add(target)
            if from_line:
                from_node = alias_node.add(from_line)
                subtree = self._build_all_tree(target, visited, resolved_targets)
                if subtree:
                    self._merge_tree(from_node, subtree)
            else:
                subtree = self._build_all_tree(target, visited, resolved_targets)
                if subtree:
                    self._merge_tree(alias_node, subtree)
        visited.discard(fqn)
        return root

    @staticmethod
    def _merge_tree(parent: Tree, subtree: Tree) -> Tree:
        node = parent.add(subtree.label)
        for child in subtree.children:
            node.children.append(child)
        return node

    def _print_all_compact(self, module_fqn: str, symbol_name: str) -> None:
        terminals = self.resolve_all(module_fqn, symbol_name)
        if not terminals:
            self.console.print(
                f"[red]Entity not found:[/red] {module_fqn}.{symbol_name}"
            )
            return
        for ent in terminals:
            meta = json.loads(ent["metadata_json"])
            sp = self._get_source_path(ent)
            lineno = meta.get("lineno", "")
            loc = f" @ {sp}:{lineno}" if sp and lineno else ""
            self.console.print(f"{ent['type']}  {ent['full_path']}{loc}")
