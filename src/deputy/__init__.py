import json
import typer
from rich.console import Console
from rich.table import Table
from deputy._version import __version__
from deputy.logger import init_logging
from deputy.tools import (
    build_entity_tree,
    init_database,
    run_sync,
    search_entities,
    get_entity_info,
    InteractiveResolver,
)
from deputy.tools.utils import _open_database
from deputy.utils.config_file import read_config, write_config

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
    "visibility": "Visibility modifier",
    "exported": "Whether exported in __all__",
}

DEFAULT_COLUMNS = ["full_path", "language", "type", "source"]

app = typer.Typer(no_args_is_help=True)
console = Console()

@app.callback()
def cli_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-error output"),
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
    sync_deps: bool = typer.Option(None, "--sync-deps", help="Sync dependency packages from .venv"),
    no_sync_deps: bool = typer.Option(None, "--no-sync-deps", help="Skip dependency sync"),
) -> None:
    resolved = sync_deps
    if no_sync_deps and resolved is None:
        resolved = False
    try:
        run_sync(force, resolved)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print("[yellow]Sync complete[/yellow]")

@app.command(name="search")
def search(
    pattern: str = typer.Argument(..., help="Regular expression pattern"),
    type_filter: list[str] = typer.Option(None, "--type", "-t", help="Filter by entity type (repeatable)"),
    language: str = typer.Option(None, "--language", "-l", help="Filter by language"),
    limit: int = typer.Option(None, "--limit", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Result offset"),
    exact: bool = typer.Option(False, "--exact", "-e", help="Exact match on full_path"),
    name_only: bool = typer.Option(False, "--name-only", "-n", help="Match name only, not full_path"),
    show_fqn: bool = typer.Option(False, "--fqn", "-f", help="Show full path in tree output"),
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
        raise typer.Exit(code=1)

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

def _format_range(entity: dict, meta: dict, col: str, extracted: dict | None = None) -> str:
    if extracted and col in extracted:
        return extracted[col]
    start = meta.get(f"{col}_lineno")
    end = meta.get(f"{col}_end_lineno")
    if start is None:
        return ""
    path = _get_file_path(entity.get("_source", ""))
    loc = f"{path}:{start}" if start == end else f"{path}:{start}-{end}"
    return loc

def _get_column_value(entity: dict, col: str, meta: dict, extracted: dict | None = None) -> str:
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
    return ""
    if col == "decorators":
        return ", ".join(meta.get("decorators", []))
    if col == "parent_classes":
        return ", ".join(meta.get("parent_classes", []))
    if col == "visibility":
        return meta.get("visibility", "")
    if col == "exported":
        return str(meta.get("exported", ""))
    return ""

def _display_info_single(entity: dict, columns: list[str], extract: bool) -> None:
    meta = json.loads(entity["metadata_json"])
    extracted = entity.get("_extracted")
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    for col in columns:
        val = _get_column_value(entity, col, meta, extracted)
        table.add_row(f"{col}:", val)
    console.print(table)

def _display_info_table(entities: list[dict], columns: list[str]) -> None:
    meta_list = [json.loads(e["metadata_json"]) for e in entities]
    extracted_list = [e.get("_extracted") for e in entities]
    table = Table(*columns)
    for i, entity in enumerate(entities):
        row = [_get_column_value(entity, col, meta_list[i], extracted_list[i]) for col in columns]
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
    all_matches: bool = typer.Option(False, "--all", "-a", help="Show all matching entities"),
    columns: str = typer.Option(None, "--columns", "-c", help="Comma-separated columns to display (use --list-columns to see available)"),
    list_columns: bool = typer.Option(False, "--list-columns", help="List available columns and descriptions"),
    type_filter: str = typer.Option(None, "--type", "-t", help="Filter by entity type (e.g. FUNCTION, CLASS)"),
    lineno: int = typer.Option(None, "--lineno", help="Filter by line number"),
    extract: bool = typer.Option(False, "--extract", "-x", help="Extract and display actual source text for signature/docstring"),
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
        raise typer.Exit(code=1)

    if all_matches:
        if not result:
            console.print(f"[red]Entity not found:[/red] {full_path}")
            raise typer.Exit()
        _display_info_table(result, col_list)
        return

    if result is None:
        console.print(f"[red]Entity not found:[/red] {full_path}")
        raise typer.Exit()

    match_count = result.pop("_match_count", 1)
    _display_info_single(result, col_list, extract)

    if match_count > 1 and not type_filter and lineno is None:
        console.print(f"\n[dark_orange]Found {match_count} matching entities. Use --all to see all, or filter with --type / --lineno. See --help for details.[/dark_orange]")

# TODO: Allow user to go back a step, and also go forward to the next step if they had gone back a path
@app.command(name="resolve")
def resolve(
    symbol: str = typer.Argument(..., help="Symbol to resolve, in the form <module_fqn>.<symbol_name>"),
    auto: bool = typer.Option(False, "--auto", help="Only stop when multiple choices exist"),
    step: bool = typer.Option(False, "--step", help="Stop at every step regardless of ambiguity"),
    all: bool = typer.Option(False, "--all", help="Show all possible resolutions"),
    compact: bool = typer.Option(False, "--compact", help="Compact output with --all (terminal entities only)"),
) -> None:
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        console.print("[red]Symbol must be in the form <module_fqn>.<symbol_name>[/red]")
        raise typer.Exit(code=1)
    module_fqn, symbol_name = parts

    try:
        conn = _open_database()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if compact and not all:
        console.print("[red]--compact requires --all[/red]")
        raise typer.Exit(code=1)

    resolver = InteractiveResolver(conn, mode="default")
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
