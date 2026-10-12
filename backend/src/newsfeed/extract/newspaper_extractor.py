"""A fallback extractor built on newspaper4k (FR-5).

newspaper4k can fetch pages itself, but here it only ever parses the HTML it is
given: ``Article.download(input_html=...)`` skips the download, and the config
turns off image downloads (used to size the top image) and meta-refresh
following, the two parse-time paths that would otherwise make requests. Its
HTML passes through ``sanitize_html`` like every other extractor's.
"""

from __future__ import annotations

import warnings

from newsfeed.extract.common import DEFAULT_MIN_WORDS, build_result, run_safely
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionResult

with warnings.catch_warnings():
    # newspaper4k warns at import time that nltk (its optional ``nlp`` extra) is
    # missing. We don't use its NLP features, so the warning is only noise.
    warnings.filterwarnings("ignore", message=r"nltk is not installed", category=UserWarning)
    from newspaper import Article, Config

__all__ = ["Newspaper4kExtractor"]

log = get_logger(__name__)


class Newspaper4kExtractor:
    """Extract an article's main content and metadata with newspaper4k.

    Statuses are graded by word count exactly as in ``TrafilaturaExtractor``,
    and any exception is returned as ``failed`` with the error recorded. Safe
    to call from several threads: each call builds its own ``Article`` and
    ``Config``.
    """

    def __init__(self, *, min_words: int = DEFAULT_MIN_WORDS) -> None:
        """Create the extractor; ``min_words`` must be at least 1."""
        if min_words < 1:
            raise ValueError(f"min_words must be at least 1, got {min_words}")
        self._min_words = min_words

    @property
    def name(self) -> str:
        """Extractor name recorded as ``ExtractionResult.method``."""
        return "newspaper4k"

    @property
    def min_words(self) -> int:
        """Word count at or above which an extraction counts as ``ok``."""
        return self._min_words

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Extract the main content of ``html``, fetched from ``url``. Never raises."""
        return run_safely(self.name, url, lambda: self._extract(html, url), logger=log)

    def _extract(self, html: str, url: str) -> ExtractionResult:
        article = Article(url, config=_offline_config())
        article.download(input_html=html)
        article.parse()
        return build_result(
            method=self.name,
            raw_html=article.article_html,
            url=url,
            min_words=self._min_words,
            title=article.title,
            author=", ".join(article.authors) or None,
            published_at=article.publish_date,
            lead_image=article.top_image,
        )


def _offline_config() -> Config:
    """A newspaper4k config that never touches the network while parsing."""
    config = Config()
    # With images on, newspaper downloads candidate images to pick the largest
    # as the top image; off, it uses the page's og:image/meta image instead.
    config.fetch_images = False
    config.follow_meta_refresh = False
    config.memorize_articles = False
    return config
