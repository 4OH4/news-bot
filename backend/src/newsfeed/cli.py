"""Command-line entry point: ``newsfeed run|ingest|extract|eval``."""

from __future__ import annotations

import typer

from newsfeed.logging import get_logger

app = typer.Typer(help="Personal news feed pipeline.", no_args_is_help=True)
log = get_logger(__name__)


@app.callback()
def main() -> None:
    """Personal news feed pipeline."""


@app.command()
def run() -> None:
    """Run the full pipeline: ingest, extract, rank, publish."""
    log.info("pipeline not implemented yet", extra={"stage": "run"})
