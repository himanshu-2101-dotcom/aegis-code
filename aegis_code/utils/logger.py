"""Centralized Rich-based logger for Aegis-Code."""

from __future__ import annotations

import logging
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

_AEGIS_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "step": "bold magenta",
    }
)

console = Console(theme=_AEGIS_THEME)


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    """Return a named logger wired to a RichHandler.

    Duplicate handlers are avoided so this function is safe to call
    repeatedly for the same *name*.
    """
    logger = logging.getLogger(f"aegis.{name}")
    if not logger.handlers:
        handler = RichHandler(
            console=console,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
            show_time=True,
            show_path=False,
        )
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(message)s", datefmt="[%X]")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def step_banner(title: str, *, subtitle: str = "") -> None:
    """Print a prominent step banner to the console."""
    console.rule(f"[step]{title}[/step]", style="step")
    if subtitle:
        console.print(f"  {subtitle}", style="dim")


def success(msg: str) -> None:
    console.print(f"✅ {msg}", style="success")


def warn(msg: str) -> None:
    console.print(f"⚠️  {msg}", style="warning")


def error(msg: str) -> None:
    console.print(f"❌ {msg}", style="error")
