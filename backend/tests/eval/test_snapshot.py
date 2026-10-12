"""Tests for ``newsfeed.eval.snapshot``. All fetching goes through ``FakeFetcher``."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsfeed.eval.snapshot import (
    SnapshotError,
    SnapshotStatus,
    SnapshotSummary,
    UnsafeFixturesDirError,
    default_fixtures_dir,
    ensure_outside_git,
    format_summary,
    git_worktree_containing,
    read_snapshot,
    resolve_fixtures_dir,
    select_entries,
    snapshot_paths,
    take_snapshots,
)
from newsfeed.eval.urls import EvalEntry
from newsfeed.models import FetchResult
from tests.fakes import FakeFetcher

REPO_ROOT = Path(__file__).resolve().parents[3]
FETCHED_AT = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)

PAGE = b"<html><head><title>Invented headline</title></head><body><p>Made-up text.</p></body>"
PAGE_V2 = PAGE.replace(b"Made-up", b"Revised made-up")

ARTICLE = EvalEntry(id="example-story", url="https://news.example.com/2026/10/story")
BLOG = EvalEntry(id="blog-post", url="https://blog.example.org/post")
GONE = EvalEntry(id="gone", url="https://news.example.com/missing")


def now() -> datetime:
    return FETCHED_AT


@pytest.fixture
def fixtures_dir(tmp_path: Path) -> Path:
    return tmp_path / "eval-fixtures"


@pytest.fixture
def fetcher() -> FakeFetcher:
    fake = FakeFetcher()
    fake.add(
        ARTICLE.url,
        PAGE,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Link": '<https://news.example.com/canonical>; rel="canonical"',
            "Set-Cookie": "session=secret",
            "Server": "example",
        },
        etag='"v1"',
        final_url="https://news.example.com/2026/10/story?final",
    )
    fake.add(BLOG.url, PAGE, headers={"content-type": "text/html"})
    return fake


# --- Directory resolution ------------------------------------------------------


def test_flag_wins_over_env_and_default(tmp_path: Path) -> None:
    flag = tmp_path / "flag"
    env = {"NEWSFEED_FIXTURES_DIR": str(tmp_path / "env")}

    assert resolve_fixtures_dir(flag, environ=env, home=tmp_path / "home") == flag


def test_env_wins_over_default(tmp_path: Path) -> None:
    env = {"NEWSFEED_FIXTURES_DIR": str(tmp_path / "env")}

    assert resolve_fixtures_dir(None, environ=env, home=tmp_path / "home") == tmp_path / "env"


@pytest.mark.parametrize("env", [{}, {"NEWSFEED_FIXTURES_DIR": ""}, {"NEWSFEED_FIXTURES_DIR": " "}])
def test_default_is_under_home(tmp_path: Path, env: dict[str, str]) -> None:
    expected = tmp_path / "home" / ".newsfeed" / "eval-fixtures"

    assert resolve_fixtures_dir(None, environ=env, home=tmp_path / "home") == expected


def test_default_uses_path_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert default_fixtures_dir() == tmp_path / ".newsfeed" / "eval-fixtures"
    assert resolve_fixtures_dir(None, environ={}) == tmp_path / ".newsfeed" / "eval-fixtures"


def test_tilde_and_relative_paths_are_made_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert resolve_fixtures_dir(None, environ={"NEWSFEED_FIXTURES_DIR": "~/snaps"}) == (
        tmp_path / "snaps"
    )
    assert resolve_fixtures_dir(Path("rel")) == tmp_path / "rel"


def test_reads_process_environment_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEWSFEED_FIXTURES_DIR", str(tmp_path / "from-env"))

    assert resolve_fixtures_dir() == tmp_path / "from-env"


# --- Refusing to write inside a git working tree -------------------------------


def test_this_repo_is_refused() -> None:
    assert git_worktree_containing(REPO_ROOT / "eval" / "fixtures") is not None
    with pytest.raises(UnsafeFixturesDirError, match="inside the git working tree"):
        ensure_outside_git(REPO_ROOT / "eval" / "fixtures")


@pytest.mark.parametrize("git_kind", ["dir", "file"])
def test_any_git_working_tree_is_refused(tmp_path: Path, git_kind: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    if git_kind == "dir":
        (repo / ".git").mkdir()
    else:  # a `git worktree` checkout has a .git file
        (repo / ".git").write_text("gitdir: /elsewhere\n")

    assert git_worktree_containing(repo / "not" / "yet" / "created") == repo
    with pytest.raises(UnsafeFixturesDirError):
        ensure_outside_git(repo / "not" / "yet" / "created")


def test_symlink_into_a_repo_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "snaps").mkdir()
    link = tmp_path / "outside-link"
    link.symlink_to(repo / "snaps")

    with pytest.raises(UnsafeFixturesDirError):
        ensure_outside_git(link)


def test_directory_outside_any_repo_is_accepted(fixtures_dir: Path) -> None:
    assert git_worktree_containing(fixtures_dir) is None
    ensure_outside_git(fixtures_dir)


def test_take_snapshots_refuses_repo_before_fetching(tmp_path: Path, fetcher: FakeFetcher) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    with pytest.raises(UnsafeFixturesDirError):
        take_snapshots([ARTICLE], fetcher, repo / "fixtures", now=now)

    assert fetcher.calls == []
    assert not (repo / "fixtures").exists()


# --- Taking snapshots ----------------------------------------------------------


def test_snapshot_writes_gzipped_html_and_meta(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    summary = take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)

    assert [o.status for o in summary.outcomes] == [SnapshotStatus.FETCHED]
    paths = snapshot_paths(fixtures_dir, ARTICLE)
    assert paths.html == fixtures_dir / "news.example.com" / "example-story.html.gz"
    assert paths.meta == fixtures_dir / "news.example.com" / "example-story.meta.json"
    assert gzip.decompress(paths.html.read_bytes()) == PAGE

    meta = json.loads(paths.meta.read_text())
    assert meta == {
        "id": "example-story",
        "url": ARTICLE.url,
        "final_url": "https://news.example.com/2026/10/story?final",
        "status": 200,
        "fetched_at": "2026-10-12T09:30:00Z",
        "content_type": "text/html; charset=utf-8",
        "headers": {
            "content-type": "text/html; charset=utf-8",
            "etag": '"v1"',
            "link": '<https://news.example.com/canonical>; rel="canonical"',
        },
        "sha256": hashlib.sha256(PAGE).hexdigest(),
        "error": None,
    }


def test_read_snapshot_round_trips(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)

    meta, html = read_snapshot(snapshot_paths(fixtures_dir, ARTICLE))

    assert html == PAGE
    assert meta.status == 200
    assert meta.fetched_at == FETCHED_AT
    assert meta.has_html


def test_binary_body_round_trips_byte_for_byte(fixtures_dir: Path) -> None:
    body = bytes(range(256)) * 50 + "café".encode("latin-1")
    fake = FakeFetcher({ARTICLE.url: body})

    take_snapshots([ARTICLE], fake, fixtures_dir, now=now)

    assert read_snapshot(snapshot_paths(fixtures_dir, ARTICLE))[1] == body


def test_second_run_skips_and_changes_nothing(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    take_snapshots([ARTICLE, BLOG], fetcher, fixtures_dir, now=now)
    files = sorted(p for p in fixtures_dir.rglob("*") if p.is_file())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    fetcher.calls.clear()

    summary = take_snapshots([ARTICLE, BLOG], fetcher, fixtures_dir, now=now)

    assert [o.status for o in summary.outcomes] == [SnapshotStatus.SKIPPED] * 2
    assert fetcher.calls == []
    after = sorted(p for p in fixtures_dir.rglob("*") if p.is_file())
    assert after == files
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in after} == before


def test_refresh_fetches_again_and_is_deterministic(
    fixtures_dir: Path, fetcher: FakeFetcher
) -> None:
    take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)
    paths = snapshot_paths(fixtures_dir, ARTICLE)
    first = paths.html.read_bytes(), paths.meta.read_bytes()

    summary = take_snapshots([ARTICLE], fetcher, fixtures_dir, refresh=True, now=now)

    assert [o.status for o in summary.outcomes] == [SnapshotStatus.FETCHED]
    assert fetcher.fetched_urls() == [ARTICLE.url, ARTICLE.url]
    # Same content and clock give byte-identical files (gzip mtime is fixed).
    assert (paths.html.read_bytes(), paths.meta.read_bytes()) == first


def test_refresh_replaces_changed_content(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)
    fetcher.add(ARTICLE.url, PAGE_V2, headers={"content-type": "text/html"})

    take_snapshots([ARTICLE], fetcher, fixtures_dir, refresh=True, now=now)

    meta, html = read_snapshot(snapshot_paths(fixtures_dir, ARTICLE))
    assert html == PAGE_V2
    assert meta.sha256 == hashlib.sha256(PAGE_V2).hexdigest()


@pytest.mark.parametrize(("status", "reason"), [(404, "HTTP 404"), (403, "HTTP 403")])
def test_http_error_writes_meta_only(fixtures_dir: Path, status: int, reason: str) -> None:
    fake = FakeFetcher()
    fake.add(GONE.url, b"<html>error page</html>", status=status)

    summary = take_snapshots([GONE], fake, fixtures_dir, now=now)

    (outcome,) = summary.outcomes
    assert outcome.status is SnapshotStatus.FAILED
    assert outcome.reason == reason
    paths = snapshot_paths(fixtures_dir, GONE)
    assert not paths.html.exists()
    meta, html = read_snapshot(paths)
    assert html is None
    assert (meta.status, meta.error, meta.sha256) == (status, reason, None)


@pytest.mark.parametrize("status", [201, 204, 206])
def test_non_200_success_status_is_not_saved(fixtures_dir: Path, status: int) -> None:
    fake = FakeFetcher()
    fake.add(GONE.url, PAGE, status=status)

    summary = take_snapshots([GONE], fake, fixtures_dir, now=now)

    assert summary.outcomes[0].reason == f"HTTP {status}"
    assert not snapshot_paths(fixtures_dir, GONE).html.exists()


def test_fetch_error_without_response_writes_meta_only(fixtures_dir: Path) -> None:
    fake = FakeFetcher(disallowed=["https://news.example.com/"])

    summary = take_snapshots([GONE], fake, fixtures_dir, now=now)

    assert summary.outcomes[0].reason == "disallowed by robots.txt"
    meta, html = read_snapshot(snapshot_paths(fixtures_dir, GONE))
    assert (meta.status, meta.error, html) == (None, "disallowed by robots.txt", None)


def test_failed_entries_are_retried_on_the_next_run(fixtures_dir: Path) -> None:
    fake = FakeFetcher()
    fake.add(GONE.url, status=None, error="connection reset")
    take_snapshots([GONE], fake, fixtures_dir, now=now)
    fake.add(GONE.url, PAGE)

    summary = take_snapshots([GONE], fake, fixtures_dir, now=now)

    assert [o.status for o in summary.outcomes] == [SnapshotStatus.FETCHED]
    assert read_snapshot(snapshot_paths(fixtures_dir, GONE))[1] == PAGE


def test_failed_refresh_keeps_the_existing_snapshot(
    fixtures_dir: Path, fetcher: FakeFetcher
) -> None:
    take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)
    paths = snapshot_paths(fixtures_dir, ARTICLE)
    before = paths.html.read_bytes(), paths.meta.read_bytes()
    fetcher.add(ARTICLE.url, status=503)

    summary = take_snapshots([ARTICLE], fetcher, fixtures_dir, refresh=True, now=now)

    assert summary.outcomes[0].status is SnapshotStatus.FAILED
    assert summary.outcomes[0].reason == "HTTP 503 (kept existing snapshot)"
    assert (paths.html.read_bytes(), paths.meta.read_bytes()) == before


def test_corrupt_snapshot_is_fetched_again(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)
    paths = snapshot_paths(fixtures_dir, ARTICLE)
    paths.html.write_bytes(gzip.compress(b"truncated"))
    with pytest.raises(SnapshotError, match="sha256"):
        read_snapshot(paths)

    summary = take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)

    assert summary.outcomes[0].status is SnapshotStatus.FETCHED
    assert read_snapshot(paths)[1] == PAGE


@pytest.mark.parametrize(
    ("html", "meta"),
    [(b"not gzip", None), (None, b"{not json"), (None, None)],
)
def test_read_snapshot_raises_for_bad_files(
    fixtures_dir: Path, fetcher: FakeFetcher, html: bytes | None, meta: bytes | None
) -> None:
    take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)
    paths = snapshot_paths(fixtures_dir, ARTICLE)
    if html is not None:
        paths.html.write_bytes(html)
    if meta is not None:
        paths.meta.write_bytes(meta)
    if html is None and meta is None:
        paths.html.unlink()

    with pytest.raises(SnapshotError):
        read_snapshot(paths)


def test_no_temporary_files_are_left_behind(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    take_snapshots([ARTICLE, BLOG], fetcher, fixtures_dir, now=now)

    names = sorted(p.name for p in fixtures_dir.rglob("*") if p.is_file())
    assert names == [
        "blog-post.html.gz",
        "blog-post.meta.json",
        "example-story.html.gz",
        "example-story.meta.json",
    ]


class ExplodingFetcher(FakeFetcher):
    def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        if url == ARTICLE.url:
            raise RuntimeError("boom")
        return super().fetch(url, etag=etag, last_modified=last_modified)


def test_one_bad_entry_does_not_stop_the_run(fixtures_dir: Path) -> None:
    fake = ExplodingFetcher({BLOG.url: PAGE})
    ipv6 = EvalEntry(id="ipv6", url="http://[::1]/page")

    summary = take_snapshots([ARTICLE, ipv6, BLOG], fake, fixtures_dir, now=now)

    assert [(o.entry_id, o.status) for o in summary.outcomes] == [
        ("example-story", SnapshotStatus.FAILED),
        ("ipv6", SnapshotStatus.FAILED),
        ("blog-post", SnapshotStatus.FETCHED),
    ]
    assert summary.outcomes[0].reason == "RuntimeError: boom"
    assert summary.outcomes[1].reason is not None
    assert "ValueError" in summary.outcomes[1].reason


def test_international_host_uses_punycode_directory(fixtures_dir: Path) -> None:
    entry = EvalEntry(id="idn", url="https://bücher.example/artikel")

    paths = snapshot_paths(fixtures_dir, entry)

    assert paths.html.parent == fixtures_dir / "xn--bcher-kva.example"


def test_unencodable_host_is_a_value_error(fixtures_dir: Path) -> None:
    entry = EvalEntry(id="bad-host", url="https://a..example/x")

    with pytest.raises(ValueError, match="cannot use host"):
        snapshot_paths(fixtures_dir, entry)


def test_not_modified_is_a_failure(fixtures_dir: Path) -> None:
    fake = FakeFetcher()
    fake.add(GONE.url, status=304)

    summary = take_snapshots([GONE], fake, fixtures_dir, now=now)

    assert summary.outcomes[0].reason == "HTTP 304 (not modified)"
    assert not snapshot_paths(fixtures_dir, GONE).html.exists()


def test_failed_write_leaves_no_temporary_file(
    monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path, fetcher: FakeFetcher
) -> None:
    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail_replace)

    summary = take_snapshots([ARTICLE], fetcher, fixtures_dir, now=now)

    assert summary.outcomes[0].reason == "OSError: disk full"
    assert [p for p in fixtures_dir.rglob("*") if p.is_file()] == []


def test_uses_real_clock_by_default(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    before = datetime.now(UTC)
    take_snapshots([ARTICLE], fetcher, fixtures_dir)

    meta, _ = read_snapshot(snapshot_paths(fixtures_dir, ARTICLE))
    assert before <= meta.fetched_at <= datetime.now(UTC)


# --- Selecting entries and reporting -------------------------------------------


def test_select_entries() -> None:
    entries = [ARTICLE, BLOG, GONE]

    assert select_entries(entries, []) == entries
    assert select_entries(entries, ["gone", "example-story"]) == [ARTICLE, GONE]
    with pytest.raises(ValueError, match="unknown entry id\\(s\\): nope, zzz"):
        select_entries(entries, ["zzz", "gone", "nope"])


def test_format_summary_lists_counts_and_failures(fixtures_dir: Path, fetcher: FakeFetcher) -> None:
    take_snapshots([BLOG], fetcher, fixtures_dir, now=now)
    summary = take_snapshots([ARTICLE, BLOG, GONE], fetcher, fixtures_dir, now=now)

    assert format_summary(summary, fixtures_dir) == (
        f"Snapshots in {fixtures_dir}: 1 fetched, 1 skipped, 1 failed\n"
        "Failed:\n"
        f"  gone ({GONE.url}): HTTP 404 (no canned response)"
    )


def test_format_summary_without_failures(fixtures_dir: Path) -> None:
    assert format_summary(SnapshotSummary(), fixtures_dir) == (
        f"Snapshots in {fixtures_dir}: 0 fetched, 0 skipped, 0 failed"
    )
