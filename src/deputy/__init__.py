import typer
from rich.console import Console
from rich.table import Table
from deputy._version import __version__
from deputy.tools import (
    init_database,
    run_sync,
    search_entities,
    get_entity_info,
)
from deputy.utils.config_file import read_config, write_config

app = typer.Typer(no_args_is_help=True)
console = Console()

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
) -> None:
    try:
        results = search_entities(pattern)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if not results:
        console.print("[yellow]No matching entities found[/yellow]")
        raise typer.Exit()

    table = Table("Name", "Type", "Language", "Full Path")
    for row in results:
        table.add_row(row["name"], row["type"], row["language"], row["full_path"])
    console.print(table)

@app.command(name="info")
def get_info(
    full_path: str = typer.Argument(..., help="Exact entity full path"),
    resolve: bool = typer.Option(False, "--resolve", "-r", help="Resolve symbol through imports/re-exports"),
    all_matches: bool = typer.Option(False, "--all", "-a", help="Return all matching entities (default returns only the first)"),
) -> None:
    try:
        result = get_entity_info(full_path, resolve, all_matches)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if all_matches:
        if not result:
            console.print(f"[red]Entity not found:[/red] {full_path}")
            raise typer.Exit()
        table = Table("Name", "Type", "Language", "Full Path")
        for row in result:
            table.add_row(row["name"], row["type"], row["language"], row["full_path"])
        console.print(table)
    else:
        if result is None:
            console.print(f"[red]Entity not found:[/red] {full_path}")
            raise typer.Exit()
        console.print(f"[bold]Name:[/bold] {result['name']}")
        console.print(f"[bold]Type:[/bold] {result['type']}")
        console.print(f"[bold]Language:[/bold] {result['language']}")
        console.print(f"[bold]Full Path:[/bold] {result['full_path']}")
        console.print("[bold]Metadata:[/bold]")
        console.print(result["metadata_json"])

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
