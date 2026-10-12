"""Command-line entry point: ``newsfeed run|ingest|extract|eval``."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from newsfeed.config import ConfigError
from newsfeed.eval.report import (
    build_report,
    compare_reports,
    load_report,
    render_summary,
    write_report,
)
from newsfeed.eval.runner import (
    default_extractors,
    filter_entries,
    git_short_sha,
    run_evaluation,
)
from newsfeed.eval.scoring import load_boilerplate
from newsfeed.eval.snapshot import (
    UnsafeFixturesDirError,
    ensure_outside_git,
    format_summary,
    resolve_fixtures_dir,
    select_entries,
    take_snapshots,
)
from newsfeed.eval.urls import find_urls_file, load_eval_urls
from newsfeed.extract.overrides import ExtractionConfig, load_extraction_config
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


def _load_overrides(urls_path: Path) -> ExtractionConfig | None:
    """Load ``config/extraction.yaml`` from ``$NEWSFEED_CONFIG_DIR`` or the repo's ``config/``."""
    env = os.environ.get("NEWSFEED_CONFIG_DIR", "").strip()
    config_dir = Path(env) if env else urls_path.resolve().parent.parent / "config"
    path = config_dir / "extraction.yaml"
    return load_extraction_config(path) if path.is_file() else None


@eval_app.command("run")
def eval_run(
    extractor: Annotated[
        list[str] | None,
        typer.Option("--extractor", metavar="NAME", help="Only this extractor (repeatable)."),
    ] = None,
    domain: Annotated[
        str | None, typer.Option("--domain", metavar="D", help="Only entries on this domain.")
    ] = None,
    compare: Annotated[
        Path | None,
        typer.Option("--compare", metavar="PATH", help="Earlier results JSON to compare against."),
    ] = None,
    fixtures_dir: Annotated[
        Path | None,
        typer.Option(
            "--fixtures-dir",
            help="Snapshot directory. Default: $NEWSFEED_FIXTURES_DIR, "
            "else ~/.newsfeed/eval-fixtures.",
        ),
    ] = None,
    urls: Annotated[
        Path | None,
        typer.Option(
            "--urls", help="Evaluation set. Default: eval/urls.yaml in this or a parent directory."
        ),
    ] = None,
) -> None:
    """Run the extractors over the saved snapshots and write a report to eval/results/.

    Works offline. Exits 0 whatever the score; non-zero only on harness errors.
    """
    urls_path = urls if urls is not None else find_urls_file(Path.cwd())
    if urls_path is None:
        typer.echo("error: eval/urls.yaml not found; pass --urls PATH", err=True)
        raise typer.Exit(1)
    try:
        entries = filter_entries(load_eval_urls(urls_path), domain)
        extractors = default_extractors(_load_overrides(urls_path))
        previous = load_report(compare) if compare is not None else None
    except (ConfigError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    if extractor:
        known = sorted(e.name for e in extractors)
        unknown = sorted(set(extractor) - set(known))
        if unknown:
            raise typer.BadParameter(
                f"unknown extractor(s) {', '.join(unknown)}; choose from {', '.join(known)}",
                param_hint="--extractor",
            )
        extractors = [e for e in extractors if e.name in set(extractor)]

    evaluation = run_evaluation(
        entries,
        resolve_fixtures_dir(fixtures_dir),
        extractors,
        load_boilerplate(urls_path.parent / "boilerplate.txt"),
    )
    report = build_report(
        evaluation,
        date=datetime.now(UTC).date().isoformat(),
        commit=git_short_sha(urls_path.parent),
    )
    comparison = compare_reports(previous, report) if previous is not None else None
    typer.echo(render_summary(report, comparison))
    if evaluation.evaluated == 0:
        typer.echo("No entries have a snapshot, so no report was written.")
        return
    md_path, json_path = write_report(report, urls_path.parent / "results", comparison)
    typer.echo(f"Wrote {md_path} and {json_path}")
