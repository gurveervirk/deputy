import typer
from rich.console import Console
from rich.table import Table
from deputy.tools import init_database, run_sync, search_entities, get_entity_info

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
) -> None:
    run_sync(force)
    console.print("[yellow]Sync complete[/yellow]")

@app.command(name="search")
def search(
    pattern: str = typer.Argument(..., help="Regular expression pattern"),
) -> None:
    results = search_entities(pattern)
    if not results:
        console.print("[yellow]No matching entities found[/yellow]")
        raise typer.Exit()

    table = Table("Name", "Type", "Language", "Full Path")
    for row in results:
        table.add_row(row["name"], row["type"], row["language"], row["full_path"])
    console.print(table)

@app.command(name="get-info")
def get_info(
    full_path: str = typer.Argument(..., help="Exact entity full path"),
) -> None:
    entity = get_entity_info(full_path)
    if entity is None:
        console.print(f"[red]Entity not found:[/red] {full_path}")
        raise typer.Exit()

    console.print(f"[bold]Name:[/bold] {entity['name']}")
    console.print(f"[bold]Type:[/bold] {entity['type']}")
    console.print(f"[bold]Language:[/bold] {entity['language']}")
    console.print(f"[bold]Full Path:[/bold] {entity['full_path']}")
    console.print(f"[bold]File Hash:[/bold] {entity['file_hash']}")
    console.print("[bold]Metadata:[/bold]")
    console.print(entity["metadata_json"])

def main() -> None:
    app()
