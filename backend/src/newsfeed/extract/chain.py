"""The extractor chain: try extractors in turn until one succeeds (FR-5, FR-6).

``ExtractorChain`` is itself an ``Extractor``. For each page it looks up the
domain's override in ``config/extraction.yaml`` (see ``overrides.py``), then:

1. ``mode: summary_only``: returns status ``skipped`` without extracting.
2. Otherwise it tries the extractors in order (the domain's ``order``, or the
   order they were given in), then the renderer if the domain has
   ``render: playwright``, stopping at the first ``ok`` result.
3. If none is ``ok``, it returns the ``partial`` result with the most words, as
   long as it has at least ``min_partial_words``. Shorter partial results are
   usually boilerplate, such as a JavaScript shell's "enable JavaScript" notice,
   so the chain returns ``failed`` instead and the article keeps its feed
   summary. Failing that, a ``paywalled`` result, and finally ``failed``.
4. If that result is not ``ok`` and ``detect_paywall`` (``paywall.py``) finds a
   subscriber gate or teaser on the page, the chain returns it as
   ``paywalled``, with the detector's reason in ``error`` (ADR-0003 decision
   2). A teaser's content and metadata are kept; publish shows a paywalled
   article as headline, summary and link. An ``ok`` result is returned as it
   is even on a page with paywall markup, because metered sites label articles
   they serve in full that way too.

Every attempt is recorded in ``ExtractionResult.attempts``, in order, and the
returned result's ``method`` names the extractor that produced it (``None`` when
no single extractor did). A domain's ``min_words`` regrades each attempt with
that threshold, so it decides both when the chain stops and the final status.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from newsfeed.extract.common import grade, run_safely
from newsfeed.extract.overrides import DomainOverride, ExtractionConfig, Mode, url_domain
from newsfeed.extract.paywall import detect_paywall
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionAttempt, ExtractionResult, ExtractionStatus
from newsfeed.ports import Extractor

__all__ = ["DEFAULT_MIN_PARTIAL_WORDS", "ExtractorChain"]

DEFAULT_MIN_PARTIAL_WORDS: Final = 50
"""Fewest words a ``partial`` result needs for the chain to return it as content.

Boilerplate that extractors mistake for content (JavaScript and cookie notices,
"subscribe to continue" teasers) is typically under 50 words, while even short
news items are longer.
"""

log = get_logger(__name__)


class ExtractorChain:
    """An ``Extractor`` that tries several extractors, with per-domain overrides.

    Construction fails with ``ValueError`` if the extractors don't match the
    overrides (an ``order`` naming an extractor that wasn't given), so a wiring
    mistake shows up at startup. Safe to call from several threads if the
    wrapped extractors are: the chain itself keeps no per-call state.
    """

    def __init__(
        self,
        extractors: Sequence[Extractor],
        overrides: ExtractionConfig | None = None,
        *,
        renderer: Extractor | None = None,
        min_partial_words: int = DEFAULT_MIN_PARTIAL_WORDS,
    ) -> None:
        """Create a chain.

        Args:
            extractors: static extractors, in the default order.
            overrides: per-domain overrides, normally from
                ``load_extraction_config``. None means no overrides.
            renderer: the extractor used for domains with ``render: playwright``
                (#15). Without one, those domains get the static extractors only.
            min_partial_words: fewest words a ``partial`` result needs to be
                returned as content (at least 1).

        Raises:
            ValueError: if ``extractors`` is empty or has duplicate names, an
                override's ``order`` names an extractor not in ``extractors``,
                or ``min_partial_words`` is below 1.
        """
        if not extractors:
            raise ValueError("an extractor chain needs at least one extractor")
        names = [extractor.name for extractor in extractors]
        if renderer is not None:
            names.append(renderer.name)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate extractor names: {', '.join(duplicates)}")
        if min_partial_words < 1:
            raise ValueError(f"min_partial_words must be at least 1, got {min_partial_words}")

        self._extractors = tuple(extractors)
        self._by_name = {extractor.name: extractor for extractor in extractors}
        self._overrides = overrides or ExtractionConfig()
        self._renderer = renderer
        self._min_partial_words = min_partial_words
        self._check_overrides()

    def _check_overrides(self) -> None:
        for domain, override in self._overrides.domains.items():
            missing = sorted(set(override.order or ()) - self._by_name.keys())
            if missing:
                raise ValueError(
                    f"extraction override for {domain!r} names extractor(s) "
                    f"not in the chain: {', '.join(missing)}"
                )
            if override.render is not None and self._renderer is None:
                log.warning(
                    "renderer not available; domain gets static extractors only",
                    extra={"stage": "extract", "domain": domain, "render": override.render},
                )

    @property
    def name(self) -> str:
        """Name of the chain itself; results carry the winning extractor's name."""
        return "chain"

    @property
    def extractors(self) -> tuple[Extractor, ...]:
        """The static extractors, in the default order."""
        return self._extractors

    def override_for(self, url: str) -> DomainOverride | None:
        """The domain override that applies to ``url``, if any.

        The extract stage can use this before fetching, e.g. to skip fetching a
        ``summary_only`` domain at all.
        """
        return self._overrides.override_for(url)

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Extract ``html`` (fetched from ``url``) with the chain. Never raises."""
        override = self.override_for(url)
        if override is not None and override.mode is Mode.SUMMARY_ONLY:
            return ExtractionResult(
                status=ExtractionStatus.SKIPPED,
                error=f"extraction skipped: {url_domain(url)} is configured summary_only",
            )

        min_words = override.min_words if override is not None else None
        results: list[ExtractionResult] = []
        for extractor in self._plan(override):
            result = self._attempt(extractor, html, url, min_words)
            results.append(result)
            if result.status is ExtractionStatus.OK:
                break

        attempts = [_attempt_record(result) for result in results]
        chosen = self._choose(results)
        if chosen.status is not ExtractionStatus.OK:
            chosen = _check_paywall(chosen, html, url)
        return chosen.model_copy(update={"attempts": attempts})

    def _plan(self, override: DomainOverride | None) -> list[Extractor]:
        """The extractors to try for a domain, in order."""
        if override is not None and override.order is not None:
            plan = [self._by_name[name] for name in override.order]
        else:
            plan = list(self._extractors)
        if override is not None and override.render is not None and self._renderer is not None:
            # Static extraction is cheap and often enough; rendering is the
            # expensive last resort (PROJECT_PLAN.md §6, Phase 1).
            plan.append(self._renderer)
        return plan

    def _attempt(
        self, extractor: Extractor, html: str, url: str, min_words: int | None
    ) -> ExtractionResult:
        # Extractors must not raise, but one that does mustn't stop the chain.
        result = run_safely(extractor.name, url, lambda: extractor.extract(html, url), logger=log)
        # The chain reports who produced a result by ``Extractor.name``, so the
        # attempts and the chosen ``method`` always agree.
        update: dict[str, object] = {"method": extractor.name}
        if min_words is not None and result.status in _GRADED:
            status, error = grade(result.word_count, min_words)
            update |= {"status": status, "error": error}
        result = result.model_copy(update=update)
        log.debug(
            "extractor attempt",
            extra={
                "stage": "extract",
                "url": url,
                "extractor": extractor.name,
                "status": result.status,
                "word_count": result.word_count,
            },
        )
        return result

    def _choose(self, results: list[ExtractionResult]) -> ExtractionResult:
        """Pick the result to return once no more extractors will be tried."""
        last = results[-1]
        if last.status is ExtractionStatus.OK:
            return last

        partials = [r for r in results if r.status is ExtractionStatus.PARTIAL]
        # ``max`` keeps the first of equal counts, i.e. the earlier extractor.
        best = max(partials, key=lambda r: r.word_count, default=None)
        if best is not None and best.word_count >= self._min_partial_words:
            return best

        paywalled = next((r for r in results if r.status is ExtractionStatus.PAYWALLED), None)
        if paywalled is not None:
            return paywalled

        reasons = "; ".join(f"{r.method}: {r.error or r.status}" for r in results)
        if best is not None:
            error = (
                f"no usable content: best result ({best.method}, {best.word_count} words) "
                f"is below {self._min_partial_words} words, likely boilerplate ({reasons})"
            )
        else:
            error = f"no usable content ({reasons})"
        return ExtractionResult(status=ExtractionStatus.FAILED, error=error)


# Statuses that come from grading a word count, so a domain's min_words can change them.
_GRADED: Final = frozenset({ExtractionStatus.OK, ExtractionStatus.PARTIAL})


def _check_paywall(chosen: ExtractionResult, html: str, url: str) -> ExtractionResult:
    """``chosen`` marked ``paywalled`` if the page is a gate or teaser, else unchanged."""
    if chosen.status is ExtractionStatus.PAYWALLED:
        return chosen  # An extractor already said so; keep its reason.
    try:
        reason = detect_paywall(html, url)
    except Exception as exc:  # the chain promises never to raise
        log.warning(
            "paywall detection failed",
            extra={
                "stage": "extract",
                "url": url,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return chosen
    if reason is None:
        return chosen
    log.info(
        "paywall detected",
        extra={"stage": "extract", "url": url, "reason": reason, "status": chosen.status},
    )
    return chosen.model_copy(
        update={"status": ExtractionStatus.PAYWALLED, "error": f"paywall detected: {reason}"}
    )


def _attempt_record(result: ExtractionResult) -> ExtractionAttempt:
    return ExtractionAttempt(
        method=result.method or "",
        status=result.status,
        error=result.error,
        word_count=result.word_count,
    )
