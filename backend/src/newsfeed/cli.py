"""Command-line entry point: ``newsfeed run|ingest|extract|rank|preview|eval``."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from newsfeed.config import ConfigError, load_sources
from newsfeed.eval.report import (
    build_report,
    compare_reports,
    load_report,
    render_summary,
    write_report,
)
from newsfeed.eval.runlog import build_log_records, log_path, write_log
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
from newsfeed.logging import get_logger
from newsfeed.models import RunStats, Source
from newsfeed.pipeline import (
    NotAnArticleError,
    PageUnavailableError,
    extract_saved_url,
    inspect_url,
    run_pipeline,
)
from newsfeed.ports import Fetcher
from newsfeed.preview import PreviewDirError, generate_preview
from newsfeed.rank.stage import DEFAULT_WINDOW_HOURS, load_rank_config
from newsfeed.settings import Settings, load_settings
from newsfeed.wiring import build_extractor, open_fetcher, open_services

app = typer.Typer(help="Personal news feed pipeline.", no_args_is_help=True)
log = get_logger(__name__)


@app.callback()
def main() -> None:
    """Personal news feed pipeline."""


SourceOption = Annotated[
    list[str] | None,
    typer.Option("--source", metavar="ID", help="Only this source id (repeatable)."),
]
LimitOption = Annotated[
    int | None,
    typer.Option(
        "--limit",
        min=1,
        help="Ingest: at most N new articles per source. Extract: at most N articles.",
    ),
]
SinceOption = Annotated[
    datetime | None,
    typer.Option(
        "--since",
        formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"],
        help="Ignore articles older than this (UTC if no offset is given). "
        "Ingest: by publication date; extract: by when they were ingested.",
    ),
]


WindowOption = Annotated[
    float,
    typer.Option(
        "--rank-window-hours",
        min=0.0,
        help="Rank: score articles fetched within this many hours.",
    ),
]


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _select_sources(settings: Settings, ids: list[str] | None) -> list[Source]:
    """Load the enabled sources, narrowed to ``ids`` if given."""
    try:
        sources = load_sources(settings.sources_path)
    except ConfigError as exc:
        _fail(str(exc))
    known = {source.id for source in sources}
    unknown = sorted(set(ids or []) - known)
    if unknown:
        raise typer.BadParameter(
            f"unknown source(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}",
            param_hint="--source",
        )
    return [s for s in sources if s.enabled and (not ids or s.id in ids)]


def _report(stats: RunStats) -> None:
    for stage, counts in stats.stage_counts.items():
        typer.echo(f"{stage.value}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    typer.echo(f"errors: {stats.error_count}")
    typer.echo(f"run id: {stats.run_id}")


def _run_stages(
    *,
    ingest: bool,
    extract: bool,
    rank: bool,
    source: list[str] | None,
    limit: int | None,
    since: datetime | None,
    rank_window_hours: float = DEFAULT_WINDOW_HOURS,
) -> None:
    settings = load_settings()
    try:
        sources = _select_sources(settings, source)
        # Ranking weighs every configured source, whatever --source narrows.
        all_sources = _select_sources(settings, None)
        interests = load_rank_config(settings.interests_path, all_sources) if rank else None
        with open_services(settings) as services:
            stats = run_pipeline(
                services,
                sources,
                ingest=ingest,
                extract=extract,
                rank=rank,
                interests=interests,
                rank_sources=all_sources,
                rank_window_hours=rank_window_hours,
                # Only an explicit --source narrows extract; a plain run also
                # drains pending articles of disabled/removed sources and ad hoc ones.
                extract_source_ids=source or None,
                since=_aware(since),
                limit=limit,
            )
    except ConfigError as exc:
        _fail(str(exc))
    _report(stats)


@app.command()
def run(
    source: SourceOption = None,
    limit: LimitOption = None,
    since: SinceOption = None,
    rank_window_hours: WindowOption = DEFAULT_WINDOW_HOURS,
) -> None:
    """Run the pipeline: ingest new articles, extract them, then rank recent ones."""
    _run_stages(
        ingest=True,
        extract=True,
        rank=True,
        source=source,
        limit=limit,
        since=since,
        rank_window_hours=rank_window_hours,
    )


@app.command()
def ingest(
    source: SourceOption = None, limit: LimitOption = None, since: SinceOption = None
) -> None:
    """Collect, canonicalise and dedupe new articles from the enabled sources."""
    _run_stages(ingest=True, extract=False, rank=False, source=source, limit=limit, since=since)


@app.command()
def rank(rank_window_hours: WindowOption = DEFAULT_WINDOW_HOURS) -> None:
    """Score recently fetched articles against config/interests.yaml."""
    _run_stages(
        ingest=False,
        extract=False,
        rank=True,
        source=None,
        limit=None,
        since=None,
        rank_window_hours=rank_window_hours,
    )


@app.command()
def extract(
    source: SourceOption = None,
    limit: LimitOption = None,
    since: SinceOption = None,
    url: Annotated[
        str | None,
        typer.Option("--url", help="Debug: fetch and extract this one page, then print it."),
    ] = None,
    save: Annotated[
        bool, typer.Option("--save", help="With --url: store the result like a feed article.")
    ] = False,
) -> None:
    """Fetch and extract pending articles (or, with --url, one page)."""
    if url is None:
        if save:
            raise typer.BadParameter("--save only applies with --url", param_hint="--save")
        _run_stages(ingest=False, extract=True, rank=False, source=source, limit=limit, since=since)
        return
    if source or limit is not None or since is not None:
        raise typer.BadParameter("--url can't be combined with --source, --limit or --since")
    _extract_url(url, save=save)


def _extract_url(url: str, *, save: bool) -> None:
    settings = load_settings()
    try:
        if save:
            with open_services(settings) as services:
                article = extract_saved_url(services, url)
            typer.echo(f"saved article {article.id} ({article.extraction.status.value})")
            return
        with open_fetcher(settings) as fetcher:
            result = inspect_url(fetcher, build_extractor(settings), url)
    except ConfigError as exc:
        _fail(str(exc))
    except (PageUnavailableError, NotAnArticleError) as exc:
        _fail(f"{url}: {exc}")
    typer.echo(f"status: {result.status.value} ({result.method or 'no extractor'})")
    for label, value in (
        ("title", result.title),
        ("author", result.author),
        ("published", result.published_at),
        ("image", result.lead_image),
        ("words", result.word_count),
        ("error", result.error),
    ):
        if value:
            typer.echo(f"{label}: {value}")
    typer.echo("")
    typer.echo((result.text or "")[:500])


@app.command()
def preview(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output folder. Default: <data dir>/preview."),
    ] = None,
    no_images: Annotated[
        bool, typer.Option("--no-images", help="Leave images out instead of hotlinking them.")
    ] = False,
) -> None:
    """Write a static HTML preview of the extracted articles (open index.html)."""
    settings = load_settings()
    target = out if out is not None else settings.data_dir / "preview"
    try:
        with open_services(settings) as services:
            summary = generate_preview(
                services.repository, services.blobs, target, images=not no_images
            )
    except (ConfigError, PreviewDirError) as exc:
        _fail(str(exc))
    typer.echo(
        f"wrote {summary.articles} article(s) and {summary.failures} failure(s) "
        f"({summary.pending} pending, not shown)"
    )
    typer.echo(f"open {(target / 'index.html').resolve().as_uri()}")


# --- eval ----------------------------------------------------------------------

eval_app = typer.Typer(help="Extraction evaluation.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


@contextmanager
def _open_fetcher() -> Iterator[Fetcher]:
    """Yield the fetcher for eval commands; tests replace this with a fake."""
    with open_fetcher(load_settings()) as fetcher:
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

    Also writes a per-entry detailed log to eval/logs/ (git-ignored). Works
    offline. Exits 0 whatever the score; non-zero only on harness errors.
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

    started = datetime.now(UTC)
    target = resolve_fixtures_dir(fixtures_dir)
    evaluation = run_evaluation(
        entries,
        target,
        extractors,
        load_boilerplate(urls_path.parent / "boilerplate.txt"),
    )
    commit = git_short_sha(urls_path.parent)
    report = build_report(evaluation, date=started.date().isoformat(), commit=commit)
    comparison = compare_reports(previous, report) if previous is not None else None
    typer.echo(render_summary(report, comparison))
    # The detailed log is written even when nothing was evaluated: it says why.
    records = build_log_records(
        entries,
        evaluation,
        started=started,
        commit=commit,
        fixtures_dir=target,
        urls_path=urls_path,
    )
    detail_path = write_log(records, log_path(urls_path.parent / "logs", started, commit))
    if evaluation.evaluated == 0:
        typer.echo("No entries have a snapshot, so no report was written.")
    else:
        md_path, json_path = write_report(report, urls_path.parent / "results", comparison)
        typer.echo(f"Wrote {md_path} and {json_path}")
    typer.echo(f"Detailed log: {detail_path}")
