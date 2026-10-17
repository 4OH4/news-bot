"""Tests for the static preview site, using SQLite and the local filesystem."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import Article, ArticleExtraction, ExtractionStatus
from newsfeed.preview import PreviewDirError, generate_preview
from newsfeed.preview.sanitize import prepare_content
from newsfeed.preview.site import MARKER

DIRTY = (
    "<p>Body text one.</p>"
    "<script>alert(1)</script><style>p{color:red}</style>"
    '<link rel="stylesheet" href="https://cdn.example.com/x.css">'
    '<p onclick="steal()">Click <a href="javascript:evil()">here</a>.</p>'
    '<figure><img src="https://img.example.com/a.jpg" alt="pic"></figure>'
    "<p>Body text two.</p>"
)


def _article(
    slug: str,
    status: ExtractionStatus,
    *,
    day: int,
    blobs: LocalBlobStore | None = None,
    html: str | None = None,
    error: str | None = None,
    title: str | None = None,
) -> Article:
    ref = blobs.put(f"articles/{slug}.html", html.encode()) if blobs and html else None
    return Article(
        canonical_url=f"https://{slug}.example.com/a",
        url=f"https://{slug}.example.com/a?utm=x",
        source_id="gazette",
        title=title or f"Title {slug}",
        published_at=datetime(2026, 10, day, tzinfo=UTC),
        fetched_at=datetime(2026, 10, 20, tzinfo=UTC),
        extraction=ArticleExtraction(
            status=status, method="trafilatura", word_count=42, error=error
        ),
        content_ref=ref,
    )


@pytest.fixture
def stores(tmp_path: Path) -> tuple[SqliteRepository, LocalBlobStore, list[Article]]:
    repo = SqliteRepository(tmp_path / "newsfeed.db")
    blobs = LocalBlobStore(tmp_path / "blobs")
    articles = [
        _article("old", ExtractionStatus.OK, day=1, blobs=blobs, html=DIRTY),
        _article("new", ExtractionStatus.OK, day=9, blobs=blobs, html="<p>Fresh.</p>"),
        _article("half", ExtractionStatus.PARTIAL, day=5, blobs=blobs, html="<p>Cut off.</p>"),
        _article("bad", ExtractionStatus.FAILED, day=4, error="empty <result>"),
        _article("wall", ExtractionStatus.PAYWALLED, day=3, error="paywall detected"),
        _article("wait", ExtractionStatus.PENDING, day=2),
    ]
    for article in articles:
        repo.upsert_article(article)
    yield repo, blobs, articles
    repo.close()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_writes_index_articles_and_failures(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, articles = stores
    out = tmp_path / "preview"

    summary = generate_preview(repo, blobs, out)

    assert (summary.articles, summary.failures, summary.pending) == (3, 2, 1)
    index = _read(out / "index.html")
    # Newest first, and only readable articles are listed.
    assert index.index("Title new") < index.index("Title half") < index.index("Title old")
    assert "Title bad" not in index and "Title wait" not in index
    assert "trafilatura" in index and "42 words" in index
    assert "https://old.example.com/a?utm=x" in index
    for article in articles[:3]:
        assert f'href="articles/{article.id}.html"' in index
        assert (out / "articles" / f"{article.id}.html").is_file()
    failures = _read(out / "failures.html")
    assert "empty &lt;result&gt;" in failures  # escaped
    assert "paywall detected" in failures
    assert "Title new" not in failures


def test_article_page_has_cleaned_content_and_relative_links(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, articles = stores
    generate_preview(repo, blobs, tmp_path / "p")

    page = _read(tmp_path / "p" / "articles" / f"{articles[0].id}.html")

    assert "Body text one." in page and "Body text two." in page
    assert 'href="../index.html"' in page and 'href="../failures.html"' in page
    assert "prefers-color-scheme: dark" in page and "max-width: 70ch" in page


def test_output_has_no_scripts_or_external_stylesheets(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, _ = stores
    out = tmp_path / "p"
    generate_preview(repo, blobs, out)

    pages = list(out.rglob("*.html"))
    assert len(pages) == 5
    for path in pages:
        text = _read(path).lower()
        assert "<script" not in text, path
        assert not re.search(r"<link\b", text), path
        assert "@import" not in text and "javascript:" not in text, path
        assert " onclick" not in text, path
        assert "https://cdn.example.com" not in text, path


def test_images_hotlink_by_default_and_drop_with_no_images(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, articles = stores
    page = Path("articles") / f"{articles[0].id}.html"

    generate_preview(repo, blobs, tmp_path / "with")
    generate_preview(repo, blobs, tmp_path / "without", images=False)

    assert "https://img.example.com/a.jpg" in _read(tmp_path / "with" / page)
    without = _read(tmp_path / "without" / page)
    assert "<img" not in without and "<figure" not in without
    assert "Body text two." in without


def test_missing_blob_is_reported_on_the_page(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, articles = stores
    (tmp_path / "blobs" / str(articles[1].content_ref)).unlink()

    generate_preview(repo, blobs, tmp_path / "p")

    page = _read(tmp_path / "p" / "articles" / f"{articles[1].id}.html")
    assert "Stored content is missing" in page


def test_rerun_removes_pages_of_deleted_articles(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, articles = stores
    out = tmp_path / "p"
    generate_preview(repo, blobs, out)
    repo.delete_article(articles[0].id)

    generate_preview(repo, blobs, out)

    assert not (out / "articles" / f"{articles[0].id}.html").exists()


def test_empty_database_still_produces_pages(tmp_path: Path) -> None:
    with SqliteRepository(tmp_path / "newsfeed.db") as repo:
        generate_preview(repo, LocalBlobStore(tmp_path / "blobs"), tmp_path / "p")

    assert "No extracted articles yet" in _read(tmp_path / "p" / "index.html")
    assert "No failures" in _read(tmp_path / "p" / "failures.html")


def test_prepare_content_keeps_text_and_safe_markup() -> None:
    out = prepare_content('Lead <b>bold</b> <a href="https://x.example/">link</a>', images=True)

    assert out == 'Lead <b>bold</b> <a href="https://x.example/">link</a>'
    assert prepare_content("   ", images=True) == ""


def test_cli_preview_writes_to_out(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    for name in ("url_params.yaml", "extraction.yaml"):
        (config / name).write_text("domains: {}\n")
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(tmp_path))
    out = tmp_path / "site"

    result = CliRunner().invoke(cli.app, ["preview", "--out", str(out), "--no-images"])

    assert result.exit_code == 0, result.output
    assert "wrote 3 article(s) and 2 failure(s)" in result.output
    assert (out / "index.html").is_file()


def test_refuses_unmarked_non_empty_dir_and_deletes_nothing(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, _ = stores
    out = tmp_path / "Documents"
    (out / "articles").mkdir(parents=True)
    keep = out / "articles" / "mine.html"
    keep.write_text("precious")
    index = out / "index.html"
    index.write_text("my index")

    with pytest.raises(PreviewDirError):
        generate_preview(repo, blobs, out)

    assert keep.read_text() == "precious"
    assert index.read_text() == "my index"
    assert not (out / MARKER).exists()
    assert not (out / "failures.html").exists()


def test_cli_refuses_unmarked_non_empty_dir(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    for name in ("url_params.yaml", "extraction.yaml"):
        (config / name).write_text("domains: {}\n")
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(tmp_path))
    out = tmp_path / "docs"
    out.mkdir()
    (out / "notes.txt").write_text("keep me")

    result = CliRunner().invoke(cli.app, ["preview", "--out", str(out)])

    assert result.exit_code != 0
    assert "not empty" in result.output
    assert (out / "notes.txt").read_text() == "keep me"
    assert not (out / "index.html").exists()


def test_rerun_into_marked_dir_keeps_other_files_and_removes_stale_pages(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, articles = stores
    out = tmp_path / "p"
    generate_preview(repo, blobs, out)
    assert (out / MARKER).is_file()
    (out / "articles" / "stale.html").write_text("old")
    (out / "articles" / "notes.txt").write_text("not ours")

    generate_preview(repo, blobs, out)

    assert not (out / "articles" / "stale.html").exists()
    assert (out / "articles" / "notes.txt").read_text() == "not ours"
    assert (out / "articles" / f"{articles[0].id}.html").is_file()


def test_empty_existing_dir_is_accepted(
    stores: tuple[SqliteRepository, LocalBlobStore, list[Article]], tmp_path: Path
) -> None:
    repo, blobs, _ = stores
    out = tmp_path / "empty"
    out.mkdir()

    generate_preview(repo, blobs, out)

    assert (out / "index.html").is_file()
