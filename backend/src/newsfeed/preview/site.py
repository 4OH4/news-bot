"""Render stored articles as a static, offline-friendly HTML site."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from newsfeed.logging import get_logger
from newsfeed.models import Article, ExtractionStatus
from newsfeed.ports import BlobNotFoundError, BlobStore, Repository
from newsfeed.preview.sanitize import prepare_content

__all__ = ["PreviewSummary", "generate_preview"]

log = get_logger(__name__)

_TEMPLATES = Path(__file__).parent / "templates"
# Readable means there is something to show; the rest go on the failures page.
_READABLE = (ExtractionStatus.OK, ExtractionStatus.PARTIAL)
_UNREADABLE = (ExtractionStatus.FAILED, ExtractionStatus.PAYWALLED, ExtractionStatus.SKIPPED)


@dataclass(frozen=True)
class PreviewSummary:
    """What a preview run wrote."""

    out_dir: Path
    articles: int
    failures: int
    pending: int


def _sort_key(article: Article) -> datetime:
    return article.published_at or article.fetched_at


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _load_content(blobs: BlobStore, article: Article, *, images: bool) -> str | None:
    """The cleaned HTML for ``article``, or None if it has none or the blob is gone."""
    if not article.content_ref:
        return None
    try:
        raw = blobs.get(article.content_ref)
    except BlobNotFoundError:
        log.warning("preview content missing", extra={"article_id": article.id, "stage": "preview"})
        return None
    return prepare_content(raw.decode("utf-8", errors="replace"), images=images)


def generate_preview(
    repository: Repository, blobs: BlobStore, out_dir: Path, *, images: bool = True
) -> PreviewSummary:
    """Write ``index.html``, ``failures.html`` and ``articles/<id>.html`` under ``out_dir``.

    Article pages from an earlier run are removed first, so the folder always
    matches the database. Links are relative, so it works via ``file://``.
    """
    articles = repository.list_articles()
    readable = sorted(
        (a for a in articles if a.extraction.status in _READABLE), key=_sort_key, reverse=True
    )
    failed = sorted(
        (a for a in articles if a.extraction.status in _UNREADABLE), key=_sort_key, reverse=True
    )
    pending = sum(1 for a in articles if a.extraction.status is ExtractionStatus.PENDING)

    env = _environment()
    article_dir = out_dir / "articles"
    shutil.rmtree(article_dir, ignore_errors=True)
    article_dir.mkdir(parents=True)
    page = env.get_template("article.html")
    for article in readable:
        content = _load_content(blobs, article, images=images)
        (article_dir / f"{article.id}.html").write_text(
            page.render(article=article, content=content, show_lead=images), encoding="utf-8"
        )
    index = env.get_template("index.html").render(
        articles=readable, pending=pending, failures=len(failed)
    )
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    (out_dir / "failures.html").write_text(
        env.get_template("failures.html").render(articles=failed), encoding="utf-8"
    )
    return PreviewSummary(
        out_dir=out_dir, articles=len(readable), failures=len(failed), pending=pending
    )
