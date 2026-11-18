"""Aegis-Code CLI – Typer + Rich entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax

from aegis_code import __version__

app = typer.Typer(
    name="aegis",
    help="Aegis-Code — AST-driven regression & adversarial bug fixer.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]aegis-code[/] v{__version__}")
        raise typer.Exit()


def _banner() -> None:
    banner_text = r"""
     _    _____ ____ ___ ____
    / \  | ____/ ___|_ _/ ___|
   / _ \ |  _|| |  _ | |\___ \
  / ___ \| |__| |_| || | ___) |
 /_/   \_\_____\____|___|____/   CODE
    """
    console.print(
        Panel(
            f"[bold cyan]{banner_text}[/bold cyan]\n"
            f"  [dim]AST-driven regression & adversarial bug fixer  v{__version__}[/dim]",
            border_style="cyan",
        )
    )


def _check_api_key() -> None:
    """Ensure at least one LLM API key is available."""
    openai_key = os.getenv("OPENAI_API_KEY")
    google_key = os.getenv("GOOGLE_API_KEY")

    if not openai_key and not google_key:
        console.print(
            "[bold red]Error:[/] No LLM API key found.\n\n"
            "Set one of the following environment variables:\n"
            "  • [cyan]OPENAI_API_KEY[/]  — for OpenAI / GPT models\n"
            "  • [cyan]GOOGLE_API_KEY[/]  — for Google Gemini models\n\n"
            "Optionally set [cyan]AEGIS_LLM_PROVIDER[/] to [green]openai[/] or [green]gemini[/] "
            "to force a specific backend.",
            highlight=False,
        )
        raise typer.Exit(code=1)

    provider = os.getenv("AEGIS_LLM_PROVIDER", "auto-detect")
    active_key = "OPENAI_API_KEY" if openai_key else "GOOGLE_API_KEY"
    console.print(
        f"  LLM provider: [green]{provider}[/]  (key: {active_key})"
    )


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Aegis-Code — adversarial testing & auto-patching pipeline."""


@app.command()
def scan(
    path: Path = typer.Option(
        ...,
        "--path",
        "-p",
        help="Path to the target Python project directory.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse and generate tests but do not run sandbox or apply patches.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the final diff to this file instead of stdout.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable debug-level logging.",
    ),
) -> None:
    """Run the Aegis adversarial scan on a target project.

    Example:
        aegis scan --path ./my-flask-app
    """
    _banner()

    # Validate target
    if not any(path.rglob("*.py")):
        console.print(f"[red]No Python files found in {path}[/]")
        raise typer.Exit(code=1)

    _check_api_key()

    if verbose:
        import logging

        logging.getLogger("aegis").setLevel(logging.DEBUG)

    console.print(f"  Target: [bold]{path}[/]")
    console.print()

    # Initialize DB
    from aegis_code.utils.db import get_connection, create_run, complete_run

    conn = get_connection()
    run_id = create_run(conn, str(path))

    if dry_run:
        console.print("[yellow]Dry-run mode — no sandbox or patching[/]\n")
        from aegis_code.core.parser import parse_directory

        modules = parse_directory(path)
        _print_parse_summary(modules)

        from aegis_code.core.generator import generate_adversarial_tests

        tests = generate_adversarial_tests(modules, str(path))
        _print_generated_tests(tests)

        complete_run(conn, run_id, status="dry_run")
        raise typer.Exit()

    # Full pipeline
    from aegis_code.core.graph import run_scan

    try:
        final_state = run_scan(str(path))
    except RuntimeError as exc:
        console.print(f"\n[bold red]Pipeline error:[/] {exc}")
        complete_run(conn, run_id, status="error")
        raise typer.Exit(code=1)

    # Present results
    _print_results(final_state)

    # Save diff
    diff = final_state.get("final_diff", "")
    if diff:
        if output:
            output.write_text(diff, encoding="utf-8")
            console.print(f"\nDiff written to [cyan]{output}[/]")
        else:
            console.print("\n[bold]Generated Diff:[/]")
            console.print(Syntax(diff, "diff", theme="monokai", line_numbers=True))

    # Update DB
    patches = final_state.get("patches", [])
    failures = final_state.get("triaged_failures", [])
    complete_run(
        conn,
        run_id,
        status=final_state.get("status", "completed"),
        total_failures=len(failures),
        patches_applied=sum(1 for p in patches if p.get("success")),
        final_diff=diff,
    )

    # Record individual failures
    from aegis_code.utils.db import record_failure

    for f in failures:
        record_failure(
            conn,
            run_id,
            test_name=f.get("test_name", ""),
            exception_type=f.get("exception_type", ""),
            traceback_text=f.get("traceback", ""),
            severity=f.get("severity", "medium"),
        )

    conn.close()

    if final_state.get("errors"):
        console.print("\n[yellow]Warnings / Errors:[/]")
        for err in final_state["errors"]:
            console.print(f"  ⚠️  {err}")

    console.print("\n[bold green]Scan complete.[/]")


@app.command()
def history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of recent runs to show."),
) -> None:
    """Show recent scan history from the local database."""
    from aegis_code.utils.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, target_path, started_at, status, total_failures, patches_applied "
        "FROM scan_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        console.print("[dim]No scan history found.[/]")
        raise typer.Exit()

    table = Table(title="Recent Aegis Scans", border_style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Target")
    table.add_column("Started")
    table.add_column("Status")
    table.add_column("Failures", justify="right")
    table.add_column("Patches", justify="right")

    for row in rows:
        status_style = "green" if row["status"] == "completed" else "yellow"
        table.add_row(
            str(row["id"]),
            row["target_path"],
            row["started_at"][:19],
            f"[{status_style}]{row['status']}[/]",
            str(row["total_failures"]),
            str(row["patches_applied"]),
        )

    console.print(table)


@app.command()
def clean() -> None:
    """Remove cached Docker sandbox images."""
    _banner()
    try:
        from aegis_code.core.sandbox import SandboxRunner

        runner = SandboxRunner()
        runner.cleanup_images()
        console.print("[green]Sandbox images cleaned.[/]")
    except RuntimeError as exc:
        console.print(f"[red]Cannot connect to Docker:[/] {exc}")


# ── Display helpers ───────────────────────────────────────────


def _print_parse_summary(modules) -> None:
    table = Table(title="Parsed Modules", border_style="cyan")
    table.add_column("File")
    table.add_column("Functions", justify="right")
    table.add_column("Async", justify="right")
    table.add_column("DB Calls", justify="right")
    table.add_column("Loops", justify="right")

    for m in modules:
        async_count = sum(1 for f in m.functions if f.is_async)
        db_count = sum(len(f.db_calls) for f in m.functions)
        loop_count = sum(len(f.loops) for f in m.functions)
        table.add_row(
            Path(m.file_path).name,
            str(len(m.functions)),
            str(async_count),
            str(db_count),
            str(loop_count),
        )

    console.print(table)
    console.print()


def _print_generated_tests(tests: dict[str, str]) -> None:
    for name, content in tests.items():
        console.print(f"\n[bold cyan]{name}[/]")
        console.print(Syntax(content, "python", theme="monokai", line_numbers=True))


def _print_results(state: dict) -> None:
    console.print()

    # Summary table
    table = Table(title="Scan Results", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    failures = state.get("triaged_failures", [])
    patches = state.get("patches", [])
    successful_patches = [p for p in patches if p.get("success")]

    table.add_row("Target", state.get("target_path", ""))
    table.add_row("Status", state.get("status", "unknown"))
    table.add_row("Actionable Failures", str(len(failures)))
    table.add_row("Patches Generated", str(len(patches)))
    table.add_row("Patches Applied", str(len(successful_patches)))
    table.add_row("Patch Attempts", str(state.get("patch_attempt", 0)))
    table.add_row(
        "Verification",
        "[green]PASSED[/]" if state.get("verification_passed") else "[red]FAILED[/]",
    )

    console.print(table)


if __name__ == "__main__":
    app()
