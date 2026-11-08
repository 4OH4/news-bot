"""Tests for ``newsfeed inspect articles|feedback`` (issue #265), through the CLI.

The query logic is a pure module, but the issue names no function in it, so
these tests drive the command group through Typer's ``CliRunner`` against a
real SQLite store, and check the Makefile targets with fake ``uv`` and
``gcloud`` programs.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionStatus,
    Feedback,
    FeedbackEvent,
)
from tests.fakes import InMemoryBlobStore, InMemoryRepository
from tests.test_cli import env, fetcher  # noqa: F401  (fixtures for the real-pipeline test)

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text("sources: []\n")
    (config / "url_params.yaml").write_text("domains: {}\n")
    (config / "extraction.yaml").write_text("domains: {}\n")
    data = tmp_path / "data"
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))
    return data


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


def art(
    slug: str,
    *,
    source: str = "src",
    domain: str = "example.com",
    hours: float = 1,
    score: float | None = None,
    breakdown: dict[str, float] | None = None,
    status: ExtractionStatus = ExtractionStatus.OK,
    words: int = 0,
    title: str | None = None,
    method: str | None = None,
    error_type: str | None = None,
    error: str | None = None,
) -> Article:
    return Article(
        canonical_url=f"https://{domain}/{slug}",
        url=f"https://{domain}/{slug}",
        source_id=source,
        title=title or f"Headline {slug}",
        fetched_at=ago(hours=hours),
        score=score,
        score_breakdown=breakdown or {},
        extraction=ArticleExtraction(
            status=status, word_count=words, method=method, error_type=error_type, error=error
        ),
    )


def seed(data: Path, articles: list[Article], events: list[Feedback] | None = None) -> None:
    with SqliteRepository(data / "newsfeed.db") as repo:
        for a in articles:
            repo.upsert_article(a)
        for e in events or []:
            repo.save_feedback(e)


def inspect(*args: str) -> Any:
    return runner.invoke(cli.app, ["inspect", *args])


def rows(*args: str) -> list[dict[str, Any]]:
    result = inspect(*args, "--format", "jsonl")
    assert result.exit_code == 0, result.output
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def ids(*args: str) -> list[str]:
    return [r["id"] for r in rows("articles", "--fields", "id", *args)]


def line_with(output: str, token: str) -> list[str]:
    for line in output.splitlines():
        if line.split() and line.split()[0] == token:
            return line.split()
    raise AssertionError(f"no line starting with {token!r} in:\n{output}")


# --- articles: listing -------------------------------------------------------


def test_lists_matching_articles_best_score_first_and_unscored_last(data_dir: Path) -> None:
    high = art("high", source="bbc-news", score=2.0)
    low = art("low", source="bbc-news", score=0.5)
    none = art("none", source="bbc-news")
    other = art("other", source="ars-technica", score=9.0)
    seed(data_dir, [low, none, other, high])

    result = inspect("articles", "--source", "bbc-news")

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.index(high.title) < out.index(low.title) < out.index(none.title)
    assert other.title not in out
    assert "3 of 3 matching articles shown" in out


def test_default_window_is_24_hours_and_since_widens_it(data_dir: Path) -> None:
    old = art("old", hours=30)
    new = art("new", hours=2)
    seed(data_dir, [old, new])

    assert ids() == [new.id]
    assert set(ids("--since", "2d")) == {old.id, new.id}
    assert ids("--since", "90m") == []


def test_since_accepts_a_date(data_dir: Path) -> None:
    old = art("old", hours=30)
    seed(data_dir, [old])
    yesterday = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d")

    assert ids("--since", yesterday) == [old.id]


def test_limit_truncates_and_the_footer_reports_the_total(data_dir: Path) -> None:
    seed(data_dir, [art(f"a{i}", score=float(i)) for i in range(3)])

    limited = inspect("articles", "--limit", "2")
    unlimited = inspect("articles", "--limit", "0")

    assert "2 of 3 matching articles shown" in limited.stdout
    assert "3 of 3 matching articles shown" in unlimited.stdout


def test_ties_are_broken_by_id_so_output_is_stable(data_dir: Path) -> None:
    seed(data_dir, [art(f"t{i}", score=1.0) for i in range(5)])

    first = ids("--sort", "score")

    assert first == sorted(first)
    assert ids("--sort", "score") == first


def test_sort_text_is_ascending_and_numbers_descending(data_dir: Path) -> None:
    b = art("b", title="Bravo", words=10)
    a = art("a", title="Alpha", words=300)
    c = art("c", title="Charlie", words=20)
    seed(data_dir, [b, a, c])

    assert ids("--sort", "title") == [a.id, b.id, c.id]
    assert ids("--sort", "word_count") == [a.id, c.id, b.id]


# --- articles: filters -------------------------------------------------------


def test_has_and_lacks_match_exact_keys_and_prefixes(data_dir: Path) -> None:
    sport = art("sport", breakdown={"demote:sport": -1.0, "recency": 0.5})
    shop = art("shop", breakdown={"demote:shopping": -1.0, "recency": 0.5})
    plain = art("plain", breakdown={"recency": 0.5})
    seed(data_dir, [sport, shop, plain])

    assert set(ids("--has", "demote:")) == {sport.id, shop.id}
    assert ids("--has", "demote:sport") == [sport.id]
    assert ids("--lacks", "demote:") == [plain.id]


def test_has_without_a_trailing_colon_is_not_a_prefix_match(data_dir: Path) -> None:
    muted = art("muted", breakdown={"muted": 1.0})
    longer = art("longer", breakdown={"muted_extra": 1.0})
    seed(data_dir, [muted, longer])

    assert ids("--has", "muted") == [muted.id]


def test_repeated_filters_match_any_value_and_combine_with_and(data_dir: Path) -> None:
    a = art("a", source="s1", status=ExtractionStatus.OK)
    b = art("b", source="s2", status=ExtractionStatus.FAILED)
    c = art("c", source="s3", status=ExtractionStatus.OK)
    d = art("d", source="s1", status=ExtractionStatus.PARTIAL)
    seed(data_dir, [a, b, c, d])

    assert set(ids("--source", "s1", "--source", "s2")) == {a.id, b.id, d.id}
    assert set(ids("--status", "ok", "--status", "failed")) == {a.id, b.id, c.id}
    assert ids("--source", "s1", "--status", "ok") == [a.id]


def test_domain_filter_ignores_www(data_dir: Path) -> None:
    www = art("w", domain="www.example.com")
    bare = art("b", domain="example.com")
    other = art("o", domain="other.org")
    seed(data_dir, [www, bare, other])

    assert set(ids("--domain", "example.com")) == {www.id, bare.id}
    assert set(ids("--domain", "other.org", "--domain", "example.com")) == {
        www.id,
        bare.id,
        other.id,
    }


def test_score_bounds_exclude_unscored_articles(data_dir: Path) -> None:
    low = art("low", score=0.5)
    high = art("high", score=2.0)
    none = art("none")
    seed(data_dir, [low, high, none])

    assert ids("--min-score", "1") == [high.id]
    assert ids("--max-score", "1") == [low.id]
    assert set(ids("--min-score", "0", "--max-score", "5")) == {low.id, high.id}


def test_title_match_is_a_case_insensitive_regex(data_dir: Path) -> None:
    hit = art("hit", title="Storm hits the COAST")
    miss = art("miss", title="Markets calm")
    seed(data_dir, [hit, miss])

    assert ids("--title-match", "storm.*coast") == [hit.id]


# --- articles: fields and formats --------------------------------------------


def test_jsonl_has_exactly_the_chosen_fields(data_dir: Path) -> None:
    seed(data_dir, [art("a", score=1.5), art("b")])

    out = rows("articles", "--fields", "id,score")

    assert len(out) == 2
    assert all(set(r) == {"id", "score"} for r in out)


def test_csv_has_a_header_row_with_the_chosen_fields(data_dir: Path) -> None:
    a = art("a", score=1.5, domain="www.example.com", source="s1")
    seed(data_dir, [a])

    result = inspect("articles", "--fields", "id,domain,source_id", "--format", "csv")

    assert result.exit_code == 0, result.output
    parsed = list(csv.reader(io.StringIO(result.stdout.strip())))
    assert parsed[0] == ["id", "domain", "source_id"]
    assert parsed[1] == [a.id, "example.com", "s1"]


def test_default_columns_are_the_documented_ones(data_dir: Path) -> None:
    seed(data_dir, [art("a", score=1.0, words=12)])

    out = rows("articles")

    assert list(out[0]) == ["score", "published_at", "source_id", "status", "word_count", "title"]


def test_breakdown_field_prints_keys_and_values(data_dir: Path) -> None:
    seed(data_dir, [art("a", breakdown={"recency": 0.25, "demote:sport": -1.0})])

    result = inspect("articles", "--fields", "breakdown")

    assert "recency" in result.stdout
    assert "demote:sport" in result.stdout
    assert "0.25" in result.stdout


def test_summary_is_printed_only_when_asked_for(data_dir: Path) -> None:
    a = art("a")
    a.summary = "Invented teaser sentence zebra."
    seed(data_dir, [a])

    assert "zebra" not in inspect("articles").stdout
    assert "zebra" in inspect("articles", "--fields", "id,summary").stdout


def test_extraction_fields_come_from_the_stored_article(data_dir: Path) -> None:
    a = art(
        "f",
        status=ExtractionStatus.FAILED,
        method="trafilatura",
        error_type="timeout",
        error="took too long",
        words=7,
    )
    seed(data_dir, [a])

    (row,) = rows("articles", "--fields", "status,method,error_type,error,word_count,domain")

    assert row == {
        "status": "failed",
        "method": "trafilatura",
        "error_type": "timeout",
        "error": "took too long",
        "word_count": 7,
        "domain": "example.com",
    }


# --- articles: grouping ------------------------------------------------------


def test_group_by_domain_counts_statuses_and_median_words(data_dir: Path) -> None:
    ex = [
        art("a", domain="example.com", words=400),
        art("b", domain="example.com", words=600),
        art("c", domain="example.com", words=800),
        art("d", domain="example.com", status=ExtractionStatus.FAILED),
    ]
    seed(data_dir, [*ex, art("e", domain="other.org", words=100)])

    result = inspect("articles", "--group-by", "domain")

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.index("example.com") < out.index("other.org")
    tokens = line_with(out, "example.com")
    assert "4" in tokens  # count
    assert "3" in tokens  # ok
    assert "1" in tokens  # failed
    assert 500.0 in [float(t) for t in tokens[1:] if t.replace(".", "", 1).isdigit()]
    assert "1" in line_with(out, "other.org")


def test_group_by_reports_blank_score_stats_when_nothing_is_scored(data_dir: Path) -> None:
    seed(data_dir, [art("a", domain="u.example"), art("b", domain="s.example", score=2.0)])

    out = rows("articles", "--group-by", "domain")

    by_domain = {r["domain"]: r for r in out}
    assert by_domain["u.example"]["count"] == 1
    assert by_domain["u.example"]["score_mean"] in (None, "")
    assert by_domain["s.example"]["score_mean"] == 2.0


def test_group_by_breakdown_counts_each_key_once_per_article(data_dir: Path) -> None:
    seed(
        data_dir,
        [
            art("a", breakdown={"recency": 0.5, "topic:ai": 1.0}),
            art("b", breakdown={"recency": 0.1}),
        ],
    )

    out = inspect("articles", "--group-by", "breakdown").stdout

    assert "2" in line_with(out, "recency")
    assert "1" in line_with(out, "topic:ai")


@pytest.mark.parametrize("key", ["source_id", "domain", "status", "method", "error_type"])
def test_every_documented_group_key_works(data_dir: Path, key: str) -> None:
    seed(data_dir, [art("a", method="m", error_type="timeout")])

    result = inspect("articles", "--group-by", key, "--format", "csv")

    assert result.exit_code == 0, result.output
    assert "count" in result.stdout.splitlines()[0]


# --- invalid options ---------------------------------------------------------


def test_unknown_field_exits_2_and_lists_the_valid_fields(data_dir: Path) -> None:
    result = inspect("articles", "--fields", "id,body")

    assert result.exit_code == 2
    for name in ("id", "source_id", "word_count", "breakdown", "summary"):
        assert name in result.output


def test_malformed_duration_exits_2_and_explains_the_format(data_dir: Path) -> None:
    result = inspect("articles", "--since", "3x")

    assert result.exit_code == 2
    assert "90m" in result.output
    assert "24h" in result.output
    assert "7d" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["articles", "--sort", "nonsense"],
        ["articles", "--group-by", "nonsense"],
        ["articles", "--min-score", "3", "--max-score", "1"],
        ["articles", "--format", "xml"],
        ["feedback", "--event", "nonsense"],
        ["feedback", "--group-by", "nonsense"],
        ["feedback", "--since", "3x"],
    ],
)
def test_invalid_options_exit_2(data_dir: Path, args: list[str]) -> None:
    result = inspect(*args)

    assert result.exit_code == 2
    assert "No such command" not in result.output  # the command exists; the option is rejected


def test_invalid_group_key_names_the_valid_choices(data_dir: Path) -> None:
    result = inspect("articles", "--group-by", "nonsense")

    assert "source_id" in result.output
    assert "error_type" in result.output


# --- feedback ----------------------------------------------------------------


def _feedback_store(data: Path) -> tuple[Article, Feedback, Feedback, Feedback]:
    kept = art("kept", source="src-a", title="Kept headline")
    gone = art("gone", source="src-b", title="Gone headline")  # never stored
    like1 = Feedback(article_id=gone.id, event=FeedbackEvent.LIKE, created_at=ago(hours=3))
    like2 = Feedback(article_id=kept.id, event=FeedbackEvent.LIKE, created_at=ago(hours=2))
    dismiss = Feedback(article_id=kept.id, event=FeedbackEvent.DISMISS, created_at=ago(hours=1))
    seed(data, [kept], [like1, like2, dismiss])
    return kept, like1, like2, dismiss


def test_feedback_lists_newest_first_with_blank_title_for_a_missing_article(
    data_dir: Path,
) -> None:
    kept, like1, _, _ = _feedback_store(data_dir)

    out = rows("feedback")

    assert [r["event"] for r in out] == ["dismiss", "like", "like"]
    assert out[0]["title"] == kept.title
    assert out[0]["source_id"] == "src-a"
    assert out[2]["article_id"] == like1.article_id
    assert out[2]["title"] in (None, "")
    assert out[2]["source_id"] in (None, "")


def test_feedback_group_by_event_counts(data_dir: Path) -> None:
    _feedback_store(data_dir)

    out = inspect("feedback", "--group-by", "event").stdout

    assert "2" in line_with(out, "like")
    assert "1" in line_with(out, "dismiss")


def test_feedback_group_by_source_id(data_dir: Path) -> None:
    _feedback_store(data_dir)

    out = rows("feedback", "--group-by", "source_id")

    counts = {r["source_id"]: r["count"] for r in out}
    assert counts["src-a"] == 2


def test_feedback_event_filter_limit_and_default_window(data_dir: Path) -> None:
    kept = art("k")
    old = Feedback(article_id=kept.id, event=FeedbackEvent.OPEN, created_at=ago(days=9))
    likes = [
        Feedback(article_id=kept.id, event=FeedbackEvent.LIKE, created_at=ago(hours=h))
        for h in (1, 2, 3)
    ]
    seed(data_dir, [kept], [old, *likes])

    assert {r["event"] for r in rows("feedback")} == {"like"}  # default 7d drops the old one
    assert len(rows("feedback", "--since", "14d")) == 4
    assert len(rows("feedback", "--event", "like", "--limit", "2")) == 2
    assert rows("feedback", "--event", "dismiss") == []


# --- read-only, no body text -------------------------------------------------


class _WriteForbidden(AssertionError):
    pass


class ReadOnlyRepository(InMemoryRepository):
    """Fails on every write method, so any write attempt is a test failure."""

    def upsert_article(self, article: Article) -> None:
        raise _WriteForbidden("upsert_article")

    def delete_article(self, article_id: str) -> bool:
        raise _WriteForbidden("delete_article")

    def save_feedback(self, feedback: Feedback) -> None:
        raise _WriteForbidden("save_feedback")

    def save_source_state(self, state: Any) -> None:
        raise _WriteForbidden("save_source_state")

    def save_run_stats(self, stats: Any) -> None:
        raise _WriteForbidden("save_run_stats")

    def seed(self, article: Article) -> None:
        InMemoryRepository.upsert_article(self, article)

    def seed_feedback(self, feedback: Feedback) -> None:
        InMemoryRepository.save_feedback(self, feedback)


class BlobsForbidden(InMemoryBlobStore):
    def get(self, ref: str) -> bytes:
        raise _WriteForbidden("blob get")

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        raise _WriteForbidden("blob put")

    def exists(self, ref: str) -> bool:
        raise _WriteForbidden("blob exists")


ALL_INVOCATIONS = [
    ["articles"],
    ["articles", "--source", "s", "--status", "ok", "--domain", "example.com"],
    ["articles", "--min-score", "0", "--has", "recency", "--lacks", "demote:"],
    ["articles", "--title-match", "head", "--fields", "id,summary,breakdown", "--limit", "0"],
    ["articles", "--format", "jsonl"],
    ["articles", "--format", "csv"],
    ["articles", "--group-by", "source_id"],
    ["articles", "--group-by", "domain"],
    ["articles", "--group-by", "status"],
    ["articles", "--group-by", "method"],
    ["articles", "--group-by", "error_type"],
    ["articles", "--group-by", "breakdown", "--format", "jsonl"],
    ["feedback"],
    ["feedback", "--format", "csv"],
    ["feedback", "--event", "like", "--limit", "1"],
    ["feedback", "--group-by", "event"],
    ["feedback", "--group-by", "source_id"],
]


@pytest.mark.parametrize("args", ALL_INVOCATIONS, ids=[" ".join(a) for a in ALL_INVOCATIONS])
def test_no_subcommand_writes_or_reads_blobs(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    repo = ReadOnlyRepository()
    a = art("a", score=1.0, breakdown={"recency": 0.1}, words=5)
    a.content_ref = "memory://articles/a.html"
    repo.seed(a)
    repo.seed_feedback(Feedback(article_id=a.id, event=FeedbackEvent.LIKE, created_at=ago(hours=1)))
    monkeypatch.setattr(cli, "open_storage", lambda settings: (repo, BlobsForbidden()))

    result = inspect(*args)

    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"


def test_a_failing_config_exits_with_a_message_and_no_traceback(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(data_dir / "missing"))
    monkeypatch.setenv("NEWSFEED_STORAGE", "no-such-backend")

    result = inspect("articles")

    assert "No such command" not in result.output
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "Traceback" not in result.output


# --- through the real pipeline -----------------------------------------------


def test_inspect_reads_fields_the_real_pipeline_sets(env: Path) -> None:  # noqa: F811
    assert runner.invoke(cli.app, ["run"]).exit_code == 0

    out = rows(
        "articles", "--fields", "id,domain,status,method,word_count,source_id", "--limit", "0"
    )

    assert len(out) == 4
    assert {r["source_id"] for r in out} == {"gazette"}
    ok = [r for r in out if r["status"] == "ok"]
    assert ok, out
    assert all(r["method"] and r["word_count"] > 0 and r["domain"] for r in ok)
    grouped = inspect("articles", "--group-by", "domain").stdout
    assert "news.example.com" in grouped


# --- Makefile ----------------------------------------------------------------


def _fake_bin(tmp_path: Path, project: str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "uv-called.txt"
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(f"#!/bin/sh\nprintf '%s' '{project}'\n")
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{marker}"\n'
        f'echo "storage=$NEWSFEED_STORAGE bucket=$NEWSFEED_GCS_BUCKET" >> "{marker}"\n'
    )
    for p in (gcloud, uv):
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return bin_dir, marker


def _make(
    tmp_path: Path, project: str, *args: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir, marker = _fake_bin(tmp_path, project)
    environ = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path),
    }
    proc = subprocess.run(
        ["make", "-C", str(REPO_ROOT), *args],
        env=environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return proc, marker


def test_make_inspect_gcp_stops_before_python_when_gcloud_has_no_project(tmp_path: Path) -> None:
    proc, marker = _make(tmp_path, "", "inspect-gcp", "ARGS=articles --since 24h")

    assert proc.returncode != 0
    assert "gcloud" in (proc.stdout + proc.stderr)
    assert not marker.exists()


def test_make_inspect_gcp_targets_the_live_store_of_the_configured_project(
    tmp_path: Path,
) -> None:
    proc, marker = _make(tmp_path, "demo-proj", "inspect-gcp", "ARGS=articles --source bbc-news")

    assert proc.returncode == 0, proc.stderr
    called = marker.read_text()
    assert "inspect articles --source bbc-news" in called
    assert "storage=gcp bucket=demo-proj-content" in called


def test_make_inspect_runs_against_local_storage(tmp_path: Path) -> None:
    proc, marker = _make(tmp_path, "demo-proj", "inspect", "ARGS=feedback --group-by event")

    assert proc.returncode == 0, proc.stderr
    called = marker.read_text()
    assert "inspect feedback --group-by event" in called
    assert "storage=gcp" not in called


def test_make_help_lists_both_inspect_targets(tmp_path: Path) -> None:
    proc, _ = _make(tmp_path, "demo-proj", "help")

    assert proc.returncode == 0
    assert "inspect-gcp" in proc.stdout
    # `make help` colours the target name with an ANSI escape, so strip it before comparing.
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in proc.stdout.splitlines()]
    assert any(line.split()[:1] == ["inspect"] for line in plain)
