import contextlib
import json

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from deputy.database.sqlite import (
    delete_inheritance_pin,
    get_direct_subclasses,
    get_entity_by_id,
    get_entity_ids_by_fqn,
    get_inheritance_pin,
    get_transitive_subclasses,
    list_inheritance_pins,
    upsert_class_bases,
    upsert_inheritance_pin,
)
from deputy.logger import get_logger, init_logging
from deputy.tools import (
    InteractiveResolver,
    build_entity_tree,
    get_entity_info,
    init_database,
    run_sync,
    search_entities,
)
from deputy.tools.inheritance import eager_resolve_all_inherited_members
from deputy.tools.utils import _open_database, get_containing_module_fqn
from deputy.utils.config_file import read_config, write_config
from deputy.utils.git import get_current_branch

logger = get_logger("cli")

AVAILABLE_COLUMNS = {
    "full_path": "Entity full path",
    "language": "Language",
    "type": "Entity type",
    "lineno": "Starting line number",
    "end_lineno": "Ending line number",
    "source": "Source file:lineno",
    "signature": "Signature location as path:line or path:start-end (actual text with --extract)",
    "arguments": "Arguments location as path:line or path:start-end",
    "return_type": "Return type annotation location as path:line or path:start-end",
    "docstring": "Docstring location as path:line or path:start-end (actual text with --extract)",
    "decorators": "Decorator names",
    "parent_classes": "Parent/inherited class names",
    "resolved_bases": "Resolved base class FQNs",
    "unresolved_bases": "Unresolved base class names with candidate details",
    "mro": "Full MRO (method resolution order) chain",
    "inherited_from": "Class in the MRO that provides this member (if inherited)",
    "visibility": "Visibility modifier",
    "exported": "Whether exported in __all__",
    "annotations": "Annotations (e.g. Java annotations)",
    "superclass": "Java superclass",
    "implements": "Java implemented interfaces",
    "is_abstract": "Whether abstract (Java/Python)",
    "is_final": "Whether final (Java)",
    "is_static": "Whether static (Java/Python)",
    "requires": "Java module required modules",
    "requires_static": "Java module static-required modules",
    "requires_transitive": "Java module transitive-required modules",
    "exports": "Java module exported packages",
    "qualified_exports": "Java module qualified exports (package -> targets)",
    "opens": "Java module opened packages",
    "qualified_opens": "Java module qualified opens (package -> targets)",
    "uses": "Java module service uses",
    "provides": "Java module service provides (service -> impls)",
}

DEFAULT_COLUMNS = ["full_path", "language", "type", "source"]

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def cli_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress non-error output"
    ),
) -> None:
    if verbose:
        init_logging(level="DEBUG")
    elif quiet:
        init_logging(level="ERROR")
    else:
        init_logging()


@app.command()
def init(
    path: str = typer.Option(".deputy.db", "--path", "-p", help="Database path"),
) -> None:
    init_database(path)
    console.print(f"[green]Initialised database at[/green] [bold]{path}[/bold]")


@app.command()
def sync(
    force: bool = typer.Option(False, "--force", "-f", help="Force full re-sync"),
    sync_deps: bool = typer.Option(
        None, "--sync-deps", help="Sync dependency packages from .venv"
    ),
    no_sync_deps: bool = typer.Option(
        None, "--no-sync-deps", help="Skip dependency sync"
    ),
) -> None:
    resolved = sync_deps
    if no_sync_deps and resolved is None:
        resolved = False
    try:
        run_sync(force, resolved)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    console.print("[yellow]Sync complete[/yellow]")


@app.command(name="search")
def search(
    pattern: str = typer.Argument(..., help="Regular expression pattern"),
    type_filter: list[str] = typer.Option(
        None, "--type", "-t", help="Filter by entity type (repeatable)"
    ),
    language: str = typer.Option(None, "--language", "-l", help="Filter by language"),
    limit: int = typer.Option(None, "--limit", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Result offset"),
    exact: bool = typer.Option(False, "--exact", "-e", help="Exact match on full_path"),
    name_only: bool = typer.Option(
        False, "--name-only", "-n", help="Match name only, not full_path"
    ),
    show_fqn: bool = typer.Option(
        False, "--fqn", "-f", help="Show full path in tree output"
    ),
) -> None:
    try:
        results = search_entities(
            pattern,
            type_filter=type_filter,
            language=language,
            limit=limit,
            offset=offset,
            exact=exact,
            name_only=name_only,
        )
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    if not results:
        console.print("[yellow]No matching entities found[/yellow]")
        raise typer.Exit()

    cfg = read_config()
    display_mode = cfg.get("display_mode", "table")

    if display_mode == "tree":
        tree = build_entity_tree(results, show_fqn=show_fqn)
        console.print(tree)
    else:
        table = Table("Name", "Type", "Language", "Full Path")
        for row in results:
            table.add_row(row["name"], row["type"], row["language"], row["full_path"])
        console.print(table)


def _get_file_path(source: str) -> str:
    parts = source.rsplit(":", 1)
    return parts[0] if len(parts) > 1 else source


def _format_range(
    entity: dict, meta: dict, col: str, extracted: dict | None = None
) -> str:
    if extracted and col in extracted:
        return extracted[col]
    start = meta.get(f"{col}_lineno")
    end = meta.get(f"{col}_end_lineno")
    if start is None:
        return ""
    path = _get_file_path(entity.get("_source", ""))
    loc = f"{path}:{start}" if start == end else f"{path}:{start}-{end}"
    return loc


def _get_column_value(
    entity: dict, col: str, meta: dict, extracted: dict | None = None
) -> str:
    if col == "full_path":
        return entity["full_path"]
    if col == "language":
        return entity["language"]
    if col == "type":
        return entity["type"]
    if col == "lineno":
        return str(meta.get("lineno", ""))
    if col == "end_lineno":
        return str(meta.get("end_lineno", ""))
    if col == "source":
        return entity.get("_source", "")
    if col in ("signature", "arguments", "return_type", "docstring"):
        return _format_range(entity, meta, col, extracted)
    if col == "decorators":
        return ", ".join(meta.get("decorators", []))
    if col == "parent_classes":
        return ", ".join(meta.get("parent_classes", []))
    if col == "visibility":
        return meta.get("visibility", "")
    if col == "exported":
        return str(meta.get("exported", ""))
    if col == "annotations":
        return ", ".join(meta.get("annotations", []))
    if col == "superclass":
        return str(meta.get("superclass", ""))
    if col == "implements":
        return ", ".join(meta.get("implements", []))
    if col == "is_abstract":
        return str(meta.get("is_abstract", ""))
    if col == "is_final":
        return str(meta.get("is_final", ""))
    if col == "is_static":
        return str(meta.get("is_static", ""))
    if col in (
        "requires",
        "requires_static",
        "requires_transitive",
        "exports",
        "opens",
        "uses",
    ):
        return ", ".join(meta.get(col) or [])
    if col in ("qualified_exports", "qualified_opens", "provides"):
        return "; ".join(
            f"{k} -> {', '.join(v)}" for k, v in (meta.get(col) or {}).items()
        )
    if col == "resolved_bases":
        return _format_resolved_bases(entity)
    if col == "unresolved_bases":
        return _format_unresolved_bases(entity)
    if col == "mro":
        return _format_mro(entity)
    if col == "inherited_from":
        inherited = entity.get("_inherited_from", "")
        if inherited:
            idx = entity.get("_mro_index", 0)
            return f"{inherited} (MRO depth {idx})"
        if entity.get("type") == "INHERITED_MEMBER":
            meta_inherited = meta.get("inherited_from", "")
            if meta_inherited:
                depth = meta.get("mro_depth", 0)
                return f"{meta_inherited} (MRO depth {depth})"
        return ""
    return ""


def _format_resolved_bases(entity: dict) -> str:
    info = entity.get("_inheritance_info")
    if not info:
        return ""
    resolved = info.get("resolved_bases", [])
    return ", ".join(b["base_full_path"] for b in resolved)


def _format_unresolved_bases(entity: dict) -> str:
    """Format unresolved bases for info display.

    TODO: add --unresolved flag to search command for discovering classes with
    unresolved bases; also handle qualified base names (e.g. c.Y) in
    resolve_all_inherits and pin-inheritance
    """
    info = entity.get("_inheritance_info")
    if not info:
        return ""
    unresolved = info.get("unresolved_bases", [])
    if not unresolved:
        return ""
    parts = []
    for ub in unresolved:
        candidates = ub.get("candidates", [])
        if candidates:
            cand_info = []
            for c in candidates:
                scope = c.get("scope", "")
                loc = f"{c.get('full_path', '?')}"
                if "conditional" in scope:
                    loc += " (conditional)"
                cand_info.append(loc)
            parts.append(f"{ub['base_full_path']}: {', '.join(cand_info)}")
        else:
            parts.append(f"{ub['base_full_path']}: [no candidates]")
    entity_fqn = entity.get("full_path", "")
    hint = f"\nHint: use 'deputy resolve <module>.<name>' to trace imports, then 'deputy pin-inheritance {entity_fqn} <name> <file>:<line>' to pin"
    return "; ".join(parts) + hint


def _print_unresolved_hint(entity: dict) -> None:
    info = entity.get("_inheritance_info")
    if not info:
        return
    unresolved = info.get("unresolved_bases", [])
    if not unresolved:
        return
    base_labels = []
    for ub in unresolved:
        candidates = ub.get("candidates", [])
        if candidates:
            scope = candidates[0].get("scope", "")
            label = ub["base_full_path"]
            if "conditional" in scope:
                label += " (conditional)"
            base_labels.append(label)
        else:
            base_labels.append(f"{ub['base_full_path']} [no candidates]")
    entity_fqn = entity.get("full_path", "")
    console.print(f"\n[bold]Found unresolved bases:[/bold] {', '.join(base_labels)}")
    console.print(
        f"[dim]Hint: use 'deputy resolve <module>.<name>' to trace imports, then 'deputy pin-inheritance {entity_fqn} <name> <file>:<line>' to pin[/dim]"
    )


def _format_mro(entity: dict) -> str:
    info = entity.get("_inheritance_info")
    if not info:
        return ""
    mro = info.get("mro")
    if mro is None:
        return "[incomplete - unresolved bases]"
    return " → ".join(mro)


def _display_info_single(entity: dict, columns: list[str], extract: bool) -> None:
    meta = json.loads(entity["metadata_json"])
    extracted = entity.get("_extracted")
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    for col in columns:
        val = _get_column_value(entity, col, meta, extracted)
        table.add_row(f"{col}:", val)
    console.print(table)
    if entity.get("type") == "CLASS" and "unresolved_bases" not in columns:
        info = entity.get("_inheritance_info")
        if info and info.get("unresolved_bases"):
            _print_unresolved_hint(entity)


def _display_info_table(entities: list[dict], columns: list[str]) -> None:
    meta_list = [json.loads(e["metadata_json"]) for e in entities]
    extracted_list = [e.get("_extracted") for e in entities]
    table = Table(*columns)
    for i, entity in enumerate(entities):
        row = [
            _get_column_value(entity, col, meta_list[i], extracted_list[i])
            for col in columns
        ]
        table.add_row(*row)
    console.print(table)


def _print_available_columns() -> None:
    table = Table("Column", "Description")
    for key, desc in AVAILABLE_COLUMNS.items():
        table.add_row(key, desc)
    console.print(table)


@app.command(name="info")
def get_info(
    full_path: str = typer.Argument(None, help="Exact entity full path"),
    all_matches: bool = typer.Option(
        False, "--all", "-a", help="Show all matching entities"
    ),
    columns: str = typer.Option(
        None,
        "--columns",
        "-c",
        help="Comma-separated columns to display (use --list-columns to see available)",
    ),
    list_columns: bool = typer.Option(
        False, "--list-columns", help="List available columns and descriptions"
    ),
    type_filter: str = typer.Option(
        None, "--type", "-t", help="Filter by entity type (e.g. FUNCTION, CLASS)"
    ),
    lineno: int = typer.Option(None, "--lineno", help="Filter by line number"),
    extract: bool = typer.Option(
        False,
        "--extract",
        "-x",
        help="Extract and display actual source text for signature/docstring",
    ),
) -> None:
    if list_columns:
        _print_available_columns()
        return
    if full_path is None:
        console.print("[red]Missing argument 'FULL_PATH'.[/red]")
        raise typer.Exit(code=1)

    col_list = columns.split(",") if columns else DEFAULT_COLUMNS

    try:
        result = get_entity_info(
            full_path,
            all_matches=all_matches,
            type_filter=type_filter,
            lineno=lineno,
            extract=extract,
        )
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    if all_matches:
        if not result:
            console.print(f"[red]Entity not found:[/red] {full_path}")
            raise typer.Exit()
        assert isinstance(result, list)
        _display_info_table(result, col_list)
        return

    if result is None:
        console.print(f"[red]Entity not found:[/red] {full_path}")
        raise typer.Exit()

    assert isinstance(result, dict)
    match_count = result.pop("_match_count", 1)
    _display_info_single(result, col_list, extract)

    if match_count > 1 and not type_filter and lineno is None:
        console.print(
            f"\n[dark_orange]Found {match_count} matching entities. Use --all to see all, or filter with --type / --lineno. See --help for details.[/dark_orange]"
        )


# TODO: Allow user to go back a step, and also go forward to the next step if they had gone back a path
# TODO: Try handling module members of imported modules (eg: import a.b.c; class X(a.b.c.Base): pass) - this is tricky because we need to resolve the import chain and then find the base class in the imported module
@app.command(name="resolve")
def resolve(
    symbol: str = typer.Argument(
        ..., help="Symbol to resolve, in the form <module_fqn>.<symbol_name>"
    ),
    auto: bool = typer.Option(
        False, "--auto", help="Only stop when multiple choices exist"
    ),
    step: bool = typer.Option(
        False, "--step", help="Stop at every step regardless of ambiguity"
    ),
    all: bool = typer.Option(False, "--all", help="Show all possible resolutions"),
    compact: bool = typer.Option(
        False, "--compact", help="Compact output with --all (terminal entities only)"
    ),
    deproc: bool = typer.Option(
        False, "--deproc", help="Resolve through deproc semantics for the active branch"
    ),
) -> None:
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        console.print(
            "[red]Symbol must be in the form <module_fqn>.<symbol_name>[/red]"
        )
        raise typer.Exit(code=1)
    module_fqn, symbol_name = parts

    try:
        conn = _open_database()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    if compact and not all:
        console.print("[red]--compact requires --all[/red]")
        raise typer.Exit(code=1)

    resolver = InteractiveResolver(
        conn,
        mode="default",
        branch_name=get_current_branch(),
        backend="deproc" if deproc else "sqlite",
    )
    if all:
        if compact:
            resolver._print_all_compact(module_fqn, symbol_name)
        else:
            resolver._print_all_tree(module_fqn, symbol_name)
    else:
        mode = "step" if step else ("auto" if auto else "default")
        resolver.mode = mode
        result = resolver.resolve(module_fqn, symbol_name)
        if result is None:
            raise typer.Exit(code=1)

    conn.close()


@app.command()
def subclasses(
    full_path: str = typer.Argument(..., help="Base class FQN to find subclasses of"),
    transitive: bool = typer.Option(
        False, "--transitive", "-t", help="Include indirect subclasses"
    ),
) -> None:
    try:
        conn = _open_database()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    branch = get_current_branch()

    if transitive:
        subs = get_transitive_subclasses(conn, full_path, branch_name=branch)
    else:
        subs = get_direct_subclasses(conn, full_path, branch_name=branch)

    conn.close()

    if not subs:
        console.print(f"[yellow]No subclasses found for[/yellow] {full_path}")
        raise typer.Exit()

    tree = Tree(f"Subclasses of [bold]{full_path}[/bold]")
    for sub in subs:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            meta = json.loads(sub["metadata_json"])
        lineno = meta.get("lineno", "")
        loc = f" : {lineno}" if lineno else ""
        tree.add(f"{sub['type']} {sub['full_path']}{loc}")
    console.print(tree)


@app.command(name="pin-inheritance")
def pin_inheritance(
    class_fqn: str = typer.Argument(None, help="Class FQN to pin a base for"),
    base_name: str = typer.Argument(None, help="Base class name to resolve"),
    entity_ref: str = typer.Argument(
        None, help="file_path:lineno[:col_offset] of the candidate to pin"
    ),
    remove: bool = typer.Option(False, "--remove", "-r", help="Remove an existing pin"),
    list_pins: bool = typer.Option(
        False, "--list", "-l", help="List all pins for current branch"
    ),
) -> None:
    try:
        conn = _open_database()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    branch = get_current_branch()

    if list_pins:
        pins = list_inheritance_pins(conn, branch)
        conn.close()
        if not pins:
            console.print("[yellow]No inheritance pins set[/yellow]")
            return
        table = Table("Class", "Base Name", "Pinned Entity ID")
        for pin in pins:
            table.add_row(
                pin.get("class_fqn", pin["class_full_path"]),
                pin["base_name"],
                pin["pinned_entity_id"],
            )
        console.print(table)
        return

    if not class_fqn or not base_name:
        console.print(
            "[red]Usage: deputy pin-inheritance <class_fqn> <base_name> <file_path:lineno>[/red]"
        )
        console.print("       deputy pin-inheritance --list")
        console.print("       deputy pin-inheritance --remove <class_fqn> <base_name>")
        raise typer.Exit(code=1)

    if remove:
        ids = get_entity_ids_by_fqn(conn, class_fqn)
        if not ids:
            conn.close()
            console.print(f"[red]Class not found:[/red] {class_fqn}")
            raise typer.Exit(code=1)
        for eid in ids:
            entity = get_entity_by_id(conn, eid)
            if entity and entity["type"] == "CLASS":
                pin = get_inheritance_pin(conn, eid, base_name, branch)
                if pin:
                    pinned_entity = get_entity_by_id(conn, pin["pinned_entity_id"])
                    if pinned_entity:
                        conn.execute(
                            "DELETE FROM class_bases WHERE class_entity_id = ? AND base_full_path = ?",
                            (eid, pinned_entity["full_path"]),
                        )
                        upsert_class_bases(
                            conn,
                            eid,
                            [
                                {
                                    "base_full_path": base_name,
                                    "base_entity_id": None,
                                    "is_resolved": False,
                                }
                            ],
                        )
                delete_inheritance_pin(conn, eid, base_name, branch)
                console.print(f"[green]Removed pin for[/green] {class_fqn}:{base_name}")
                break
        else:
            console.print(f"[red]Class not found:[/red] {class_fqn}")
            raise typer.Exit(code=1)
        eager_resolve_all_inherited_members(conn, records=None, branch=branch)
        conn.commit()
        console.print(
            "[dim]Inherited member aliases re-resolved after pin removal[/dim]"
        )
        conn.close()
        return

    if not entity_ref:
        console.print("[red]Missing entity reference (file_path:lineno)[/red]")
        raise typer.Exit(code=1)

    ids = get_entity_ids_by_fqn(conn, class_fqn)
    class_entity_id = None
    for eid in ids:
        entity = get_entity_by_id(conn, eid)
        if entity and entity["type"] == "CLASS":
            class_entity_id = eid
            break

    if not class_entity_id:
        conn.close()
        console.print(f"[red]Class not found:[/red] {class_fqn}")
        raise typer.Exit(code=1)

    parts = entity_ref.rsplit(":", 2)
    lineno = int(parts[1]) if len(parts) > 1 else None
    col_offset = int(parts[2]) if len(parts) > 2 else None

    if lineno is None:
        conn.close()
        console.print(
            "[red]Entity reference must be in the form file_path:lineno[:col_offset][/red]"
        )
        raise typer.Exit(code=1)

    module_fqn = get_containing_module_fqn(conn, class_entity_id)
    if not module_fqn:
        conn.close()
        console.print(f"[red]Cannot determine module for class {class_fqn}[/red]")
        raise typer.Exit(code=1)

    module_entities = get_entity_ids_by_fqn(conn, module_fqn)
    module_entity_id = next(iter(module_entities)) if module_entities else None

    if not module_entity_id:
        conn.close()
        console.print(f"[red]Module not found: {module_fqn}[/red]")
        raise typer.Exit(code=1)

    rows = conn.execute(
        """SELECT id FROM entities
           WHERE type = 'IMPORT_ALIAS'
           AND full_path = ?
           AND json_extract(metadata_json, '$.lineno') = ?""",
        (f"{module_fqn}.{base_name}", lineno),
    ).fetchall()
    candidates = [dict(r) for r in rows]

    if col_offset is not None:
        candidates = [
            c
            for c in candidates
            if (entity := get_entity_by_id(conn, c["id"])) is not None
            and json.loads(entity["metadata_json"]).get("col_offset") == col_offset
        ]

    if not candidates:
        conn.close()
        console.print(
            f"[red]No entity found at {entity_ref} in module {module_fqn}[/red]"
        )
        raise typer.Exit(code=1)

    if len(candidates) > 1:
        conn.close()
        console.print(
            f"[red]Multiple entities found at {entity_ref}. Provide col_offset to disambiguate.[/red]"
        )
        raise typer.Exit(code=1)

    import_alias_entity = get_entity_by_id(conn, candidates[0]["id"])
    if import_alias_entity is None:
        conn.close()
        console.print(f"[red]Entity not found: {candidates[0]['id']}[/red]")
        raise typer.Exit(code=1)
    alias_meta = json.loads(import_alias_entity["metadata_json"])
    import_stmt = get_entity_by_id(conn, import_alias_entity["parent_id"])
    if import_stmt:
        import_path = import_stmt.get("name", "")
        original_name = alias_meta.get("original_name", "")
        target_fqn = f"{import_path}.{original_name}"
        target_ids = get_entity_ids_by_fqn(conn, target_fqn)
        target_entity = None
        for tid in target_ids:
            te = get_entity_by_id(conn, tid)
            if te and te["type"] == "CLASS":
                target_entity = te
                break
        if target_entity:
            pinned_entity_id = target_entity["id"]
        else:
            pinned_entity_id = import_alias_entity["id"]
    else:
        pinned_entity_id = import_alias_entity["id"]
    upsert_inheritance_pin(conn, class_entity_id, base_name, pinned_entity_id, branch)
    conn.execute(
        "DELETE FROM class_bases WHERE class_entity_id = ? AND base_full_path = ?",
        (class_entity_id, base_name),
    )
    pinned_fqn = target_entity["full_path"] if target_entity else base_name
    upsert_class_bases(
        conn,
        class_entity_id,
        [
            {
                "base_full_path": pinned_fqn,
                "base_entity_id": pinned_entity_id,
                "is_resolved": True,
            }
        ],
    )
    eager_resolve_all_inherited_members(conn, records=None, branch=branch)
    conn.commit()
    console.print(f"[green]Pinned[/green] {class_fqn}:{base_name} → {entity_ref}")
    console.print("[dim]Inherited member aliases re-resolved after pin[/dim]")
    conn.close()


@app.command()
def config(
    key: str = typer.Argument(..., help="Config key"),
    value: str = typer.Argument(None, help="Config value (omit to read)"),
) -> None:
    if value is None:
        cfg = read_config()
        if key in cfg:
            console.print(cfg[key])
        else:
            console.print(f"[red]Key not found:[/red] {key}")
            raise typer.Exit(code=1)
    else:
        write_config(key, value)
        console.print(f"[green]Set[/green] {key}={value}")


def main() -> None:
    app()
