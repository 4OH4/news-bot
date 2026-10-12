"""Save evaluation pages as snapshots outside the repo (``newsfeed eval snapshot``).

Each entry of ``eval/urls.yaml`` is fetched once through the ``Fetcher`` port
and stored in the fixtures directory as::

    <fixtures_dir>/<domain>/<id>.html.gz     the response body, gzipped
    <fixtures_dir>/<domain>/<id>.meta.json   url, final_url, status, fetched_at,
                                             content_type, selected headers, sha256

``<domain>`` is the URL's hostname. A non-200 result writes only the meta file,
so failures are visible without saving an error page. The meta file is written
last and its ``sha256`` must match the HTML, so a half-written snapshot is never
mistaken for a complete one.

Without ``--refresh``, an entry whose ``.html.gz`` exists is never fetched, even
if its meta file is missing or invalid or the HTML is corrupt; the summary lists
it as skipped and says to run ``--refresh``.

A failed fetch never deletes or overwrites an existing ``.html.gz`` or its meta
file, with or without ``--refresh``: pages that have gone offline or behind a
paywall can't be fetched again, so whatever is on disk is kept and only the
summary reports the failure. A failure is written to the meta file only when no
HTML is saved for that entry.

Snapshots are third-party content and this repo is public, so the fixtures
directory must be outside every git working tree (``ensure_outside_git``).
The scorer (#18) and the settings module (#19) reuse ``resolve_fixtures_dir``,
``snapshot_paths`` and ``read_snapshot``.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from newsfeed.eval.urls import EvalEntry
from newsfeed.logging import get_logger
from newsfeed.models import FetchResult, UtcDatetime
from newsfeed.ports import Fetcher, validate_blob_key

__all__ = [
    "FIXTURES_DIR_ENV",
    "SNAPSHOT_HEADERS",
    "SnapshotError",
    "SnapshotMeta",
    "SnapshotOutcome",
    "SnapshotPaths",
    "SnapshotStatus",
    "SnapshotSummary",
    "UnsafeFixturesDirError",
    "default_fixtures_dir",
    "ensure_outside_git",
    "format_summary",
    "git_worktree_containing",
    "read_snapshot",
    "resolve_fixtures_dir",
    "select_entries",
    "snapshot_paths",
    "take_snapshots",
]

log = get_logger(__name__)

FIXTURES_DIR_ENV = "NEWSFEED_FIXTURES_DIR"

SNAPSHOT_HEADERS = (
    "content-type",
    "content-language",
    "etag",
    "last-modified",
    "link",
    "x-robots-tag",
)
"""Response headers kept in the meta file. ``link`` can carry a canonical URL."""


# --- Locating the fixtures directory -------------------------------------------


class UnsafeFixturesDirError(ValueError):
    """The fixtures directory is inside a git working tree."""


def default_fixtures_dir(home: Path | None = None) -> Path:
    """Return the default fixtures directory, ``~/.newsfeed/eval-fixtures``."""
    return (home if home is not None else Path.home()) / ".newsfeed" / "eval-fixtures"


def resolve_fixtures_dir(
    flag: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the fixtures directory as an absolute path.

    The first of these wins: ``flag`` (``--fixtures-dir``), the
    ``NEWSFEED_FIXTURES_DIR`` environment variable (ignored if empty), then
    ``default_fixtures_dir(home)``. A leading ``~`` is expanded in all three.
    The directory isn't created or checked here; see ``ensure_outside_git``.
    """
    env = os.environ if environ is None else environ
    if flag is not None:
        path = flag
    elif env.get(FIXTURES_DIR_ENV, "").strip():
        path = Path(env[FIXTURES_DIR_ENV].strip())
    else:
        path = default_fixtures_dir(home)
    return path.expanduser().resolve()


def git_worktree_containing(path: Path) -> Path | None:
    """Return the root of the git working tree containing ``path``, or None.

    ``path`` need not exist. Symlinks are resolved first, so a link pointing into
    a repo is caught. A ``.git`` file (as in ``git worktree`` checkouts) counts
    as well as a ``.git`` directory.
    """
    resolved = path.expanduser().resolve()
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            return directory
    return None


def ensure_outside_git(path: Path) -> None:
    """Raise ``UnsafeFixturesDirError`` if ``path`` is inside a git working tree.

    Snapshots are third-party pages and the repo is public, so refusing to write
    them anywhere git could pick them up guards against committing them.
    """
    root = git_worktree_containing(path)
    if root is not None:
        raise UnsafeFixturesDirError(
            f"refusing to write snapshots to {path}: it is inside the git working tree "
            f"at {root}. Snapshots are third-party content and must stay outside the repo; "
            f"use --fixtures-dir or {FIXTURES_DIR_ENV} to choose a directory elsewhere."
        )


# --- Snapshot files ------------------------------------------------------------


class SnapshotError(Exception):
    """A snapshot on disk is missing, unreadable or doesn't match its meta file."""


class SnapshotMeta(BaseModel):
    """Contents of ``<id>.meta.json``.

    ``sha256`` is the hex digest of the uncompressed body, and is set only when
    the HTML was saved (status 200). For other results it is None and ``error``
    says what happened.
    """

    # Unknown keys are ignored rather than rejected, so a meta file written by
    # another version of this tool still reads as a valid snapshot instead of
    # making the entry look unsnapshotted (and so liable to be re-fetched).
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    url: str
    final_url: str
    status: int | None
    fetched_at: UtcDatetime
    content_type: str | None = None
    headers: dict[str, str]
    sha256: str | None = None
    error: str | None = None

    @property
    def has_html(self) -> bool:
        """True if this meta file describes a saved HTML snapshot."""
        return self.sha256 is not None


@dataclass(frozen=True)
class SnapshotPaths:
    """Where the files for one entry live."""

    html: Path
    meta: Path


def snapshot_paths(fixtures_dir: Path, entry: EvalEntry) -> SnapshotPaths:
    """Return the snapshot file paths for ``entry`` under ``fixtures_dir``.

    Raises:
        ValueError: if the URL's hostname can't be used as a directory name.
    """
    hostname = urlsplit(entry.url).hostname or ""
    try:
        domain = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"cannot use host {hostname!r} as a directory name: {exc}") from None
    # Applies the same safe-segment rules as blob keys: no '..', no separators.
    validate_blob_key(f"{domain}/{entry.id}.html.gz")
    directory = fixtures_dir / domain
    return SnapshotPaths(
        html=directory / f"{entry.id}.html.gz", meta=directory / f"{entry.id}.meta.json"
    )


def read_snapshot(paths: SnapshotPaths) -> tuple[SnapshotMeta, bytes | None]:
    """Read a snapshot: its meta, and the decompressed HTML if one was saved.

    Raises:
        SnapshotError: if the meta file is missing or invalid, or the HTML is
            missing, corrupt or doesn't match the meta file's ``sha256``.
    """
    try:
        meta = SnapshotMeta.model_validate_json(paths.meta.read_bytes())
    except (OSError, ValidationError) as exc:
        raise SnapshotError(f"{paths.meta}: cannot read snapshot meta: {exc}") from exc
    if not meta.has_html:
        return meta, None
    try:
        html = gzip.decompress(paths.html.read_bytes())
    # zlib.error comes from damaged deflate data inside an otherwise valid gzip header.
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise SnapshotError(f"{paths.html}: cannot read snapshot HTML: {exc}") from exc
    if hashlib.sha256(html).hexdigest() != meta.sha256:
        raise SnapshotError(f"{paths.html}: content doesn't match sha256 in {paths.meta.name}")
    return meta, html


def _saved_html_problem(paths: SnapshotPaths) -> str | None:
    """Say why the saved ``.html.gz`` doesn't validate, or None if it does.

    Call only when ``paths.html`` exists.
    """
    try:
        meta, _ = read_snapshot(paths)
    except SnapshotError as exc:
        return str(exc)
    if not meta.has_html:
        return f"{paths.meta.name} records a failed fetch but {paths.html.name} exists"
    return None


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- Taking snapshots ------------------------------------------------------------


class SnapshotStatus(StrEnum):
    """What happened to one entry."""

    FETCHED = "fetched"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class SnapshotOutcome:
    """The result for one entry.

    ``reason`` is set for failures, and for skipped entries whose saved HTML
    doesn't validate (so it needs ``--refresh``).
    """

    entry_id: str
    url: str
    status: SnapshotStatus
    reason: str | None = None


@dataclass
class SnapshotSummary:
    """Outcomes of one ``take_snapshots`` call, in entry order."""

    outcomes: list[SnapshotOutcome] = field(default_factory=list)

    def with_status(self, status: SnapshotStatus) -> list[SnapshotOutcome]:
        """Return the outcomes with ``status``."""
        return [o for o in self.outcomes if o.status is status]

    @property
    def fetched(self) -> list[SnapshotOutcome]:
        """Entries fetched and saved in this run."""
        return self.with_status(SnapshotStatus.FETCHED)

    @property
    def skipped(self) -> list[SnapshotOutcome]:
        """Entries skipped because their HTML was already saved."""
        return self.with_status(SnapshotStatus.SKIPPED)

    @property
    def failed(self) -> list[SnapshotOutcome]:
        """Entries that couldn't be snapshotted, with reasons."""
        return self.with_status(SnapshotStatus.FAILED)


def select_entries(entries: Sequence[EvalEntry], only: Iterable[str]) -> list[EvalEntry]:
    """Return the entries whose ids are in ``only``, or all entries if it's empty.

    Raises:
        ValueError: naming any id in ``only`` that isn't in ``entries``.
    """
    wanted = set(only)
    if not wanted:
        return list(entries)
    unknown = sorted(wanted - {entry.id for entry in entries})
    if unknown:
        raise ValueError(f"unknown entry id(s): {', '.join(unknown)}")
    return [entry for entry in entries if entry.id in wanted]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _failure_reason(result: FetchResult) -> str:
    if result.error:
        return result.error
    if result.not_modified:
        return "HTTP 304 (not modified)"
    return f"HTTP {result.status_code}"


def _snapshot_one(
    entry: EvalEntry,
    fetcher: Fetcher,
    fixtures_dir: Path,
    *,
    refresh: bool,
    now: Callable[[], datetime],
) -> SnapshotOutcome:
    paths = snapshot_paths(fixtures_dir, entry)
    has_html = paths.html.exists()
    if has_html and not refresh:
        # Never re-fetch a saved page without --refresh, even if it doesn't validate:
        # the page may now be paywalled or gone, and a 200 could be a consent page.
        problem = _saved_html_problem(paths)
        note = None if problem is None else f"{problem}; run with --refresh to replace it"
        return SnapshotOutcome(entry.id, entry.url, SnapshotStatus.SKIPPED, note)

    result = fetcher.fetch(entry.url)
    ok = result.status_code == 200 and result.error is None and not result.not_modified
    meta = SnapshotMeta(
        id=entry.id,
        url=entry.url,
        final_url=result.final_url,
        status=result.status_code,
        fetched_at=now(),
        content_type=result.headers.get("content-type"),
        headers={k: v for k, v in result.headers.items() if k in SNAPSHOT_HEADERS},
        sha256=hashlib.sha256(result.content).hexdigest() if ok else None,
        error=None if ok else _failure_reason(result),
    )

    if ok:
        # mtime=0 keeps the gzip bytes identical for identical content.
        _write_atomic(paths.html, gzip.compress(result.content, mtime=0))
        _write_atomic(paths.meta, meta.model_dump_json(indent=2).encode("utf-8"))
        return SnapshotOutcome(entry.id, entry.url, SnapshotStatus.FETCHED)

    reason = meta.error or "unknown error"
    if has_html:
        # Never touch saved HTML on a failure: the page may be gone for good (offline,
        # now paywalled), so a saved copy we can't validate is still worth keeping.
        return SnapshotOutcome(
            entry.id, entry.url, SnapshotStatus.FAILED, f"{reason} (kept existing snapshot)"
        )
    _write_atomic(paths.meta, meta.model_dump_json(indent=2).encode("utf-8"))
    return SnapshotOutcome(entry.id, entry.url, SnapshotStatus.FAILED, reason)


def take_snapshots(
    entries: Iterable[EvalEntry],
    fetcher: Fetcher,
    fixtures_dir: Path,
    *,
    refresh: bool = False,
    now: Callable[[], datetime] = _utc_now,
) -> SnapshotSummary:
    """Fetch each entry through ``fetcher`` and save it under ``fixtures_dir``.

    Entries with a saved ``.html.gz`` are never fetched unless ``refresh`` is
    True, even if the snapshot doesn't validate (meta missing, unreadable or
    mismatched, or corrupt HTML); those are skipped with a ``reason`` saying to
    use ``refresh``. An entry whose earlier fetch failed (meta file only) has no
    saved HTML, so it is tried again. A non-200 result writes only the meta file,
    and only when no HTML is saved for the entry: a failed fetch never deletes
    or overwrites an existing ``.html.gz`` or its meta file, with or without
    ``refresh``. One bad entry never stops the run: its failure is logged and
    recorded in the summary.

    Raises:
        UnsafeFixturesDirError: if ``fixtures_dir`` is inside a git working
            tree. This is checked before anything is fetched or written.
    """
    ensure_outside_git(fixtures_dir)
    summary = SnapshotSummary()
    for entry in entries:
        context = {"stage": "eval", "entry_id": entry.id, "url": entry.url}
        try:
            outcome = _snapshot_one(entry, fetcher, fixtures_dir, refresh=refresh, now=now)
        except Exception as exc:
            log.warning(
                "snapshot failed",
                extra={**context, "error_type": type(exc).__name__, "error": str(exc)},
            )
            outcome = SnapshotOutcome(
                entry.id, entry.url, SnapshotStatus.FAILED, f"{type(exc).__name__}: {exc}"
            )
        else:
            if outcome.status is SnapshotStatus.FAILED:
                log.warning("snapshot failed", extra={**context, "error": outcome.reason})
            else:
                log.info(f"snapshot {outcome.status}", extra=context)
        summary.outcomes.append(outcome)
    return summary


def format_summary(summary: SnapshotSummary, fixtures_dir: Path) -> str:
    """Render ``summary`` as the text printed by ``newsfeed eval snapshot``."""
    lines = [
        f"Snapshots in {fixtures_dir}: {len(summary.fetched)} fetched, "
        f"{len(summary.skipped)} skipped, {len(summary.failed)} failed"
    ]
    needs_refresh = [o for o in summary.skipped if o.reason]
    if needs_refresh:
        lines.append("Skipped, needs --refresh:")
        lines.extend(f"  {o.entry_id} ({o.url}): {o.reason}" for o in needs_refresh)
    if summary.failed:
        lines.append("Failed:")
        lines.extend(f"  {o.entry_id} ({o.url}): {o.reason}" for o in summary.failed)
    return "\n".join(lines)
