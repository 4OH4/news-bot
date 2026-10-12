"""Command-line entry point: ``newsfeed run|ingest|extract|eval``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from newsfeed.config import ConfigError
from newsfeed.eval.snapshot import (
    UnsafeFixturesDirError,
    ensure_outside_git,
    format_summary,
    resolve_fixtures_dir,
    select_entries,
    take_snapshots,
)
from newsfeed.eval.urls import find_urls_file, load_eval_urls
from newsfeed.fetching import DEFAULT_USER_AGENT, HttpFetcher
from newsfeed.logging import get_logger
from newsfeed.ports import Fetcher

app = typer.Typer(help="Personal news feed pipeline.", no_args_is_help=True)
log = get_logger(__name__)


@app.callback()
def main() -> None:
    """Personal news feed pipeline."""


@app.command()
def run() -> None:
    """Run the full pipeline: ingest, extract, rank, publish."""
    log.info("pipeline not implemented yet", extra={"stage": "run"})


# --- eval ----------------------------------------------------------------------

eval_app = typer.Typer(help="Extraction evaluation.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


@contextmanager
def _open_fetcher() -> Iterator[Fetcher]:
    """Yield the fetcher for eval commands; tests replace this with a fake."""
    with HttpFetcher(DEFAULT_USER_AGENT) as fetcher:
        yield fetcher


@eval_app.command("snapshot")
def eval_snapshot(
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Fetch again even if a snapshot exists.")
    ] = False,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", metavar="ID", help="Only this entry id (repeatable)."),
    ] = None,
    fixtures_dir: Annotated[
        Path | None,
        typer.Option(
            "--fixtures-dir",
            help="Snapshot directory, outside the repo. "
            "Default: $NEWSFEED_FIXTURES_DIR, else ~/.newsfeed/eval-fixtures.",
        ),
    ] = None,
    urls: Annotated[
        Path | None,
        typer.Option(
            "--urls", help="Evaluation set. Default: eval/urls.yaml in this or a parent directory."
        ),
    ] = None,
) -> None:
    """Fetch each page in eval/urls.yaml once and save it as a snapshot."""
    target = resolve_fixtures_dir(fixtures_dir)
    try:
        ensure_outside_git(target)
    except UnsafeFixturesDirError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    urls_path = urls if urls is not None else find_urls_file(Path.cwd())
    if urls_path is None:
        typer.echo("error: eval/urls.yaml not found; pass --urls PATH", err=True)
        raise typer.Exit(1)
    try:
        entries = select_entries(load_eval_urls(urls_path), only or [])
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--only") from None
    with _open_fetcher() as fetcher:
        summary = take_snapshots(entries, fetcher, target, refresh=refresh)
    typer.echo(format_summary(summary, target))
