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

@app.command(name="info")
def get_info(
    full_path: str = typer.Argument(..., help="Exact entity full path"),
    all_matches: bool = typer.Option(False, "--all", "-a", help="Return all matching entities (default returns only the first)"),
) -> None:
    try:
        result = get_entity_info(full_path, all_matches)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if all_matches:
        if not result:
            console.print(f"[red]Entity not found:[/red] {full_path}")
            raise typer.Exit()
        table = Table("Full Path", "Language", "Type", "Source")
        for row in result:
            source = row.get("_source", "")
            table.add_row(row["full_path"], row["language"], row["type"], source)
        console.print(table)
    else:
        if result is None:
            console.print(f"[red]Entity not found:[/red] {full_path}")
            raise typer.Exit()
        source = result.get("_source", "")
        loc = f" @ {source}" if source else ""
        console.print(f"{result['full_path']}  {result['language']}  {result['type']}{loc}")

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
