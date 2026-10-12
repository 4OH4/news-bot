"""Canonical URL resolution: the basis of article ids and deduplication (FR-4).

``Article.id`` is a hash of ``Article.canonical_url`` (``models.article_id``), so
two URLs for the same page must normalise to exactly the same string or the
article is stored twice. Everything here is a pure function: no fetching.

* ``normalise_url`` cleans a URL syntactically (case, ports, tracking
  parameters, query order, trailing slash, AMP variants). Per-domain parameter
  rules from ``config/url_params.yaml`` (#35) are passed in by the caller.
* ``canonical_from_html`` reads the publisher's own ``<link rel="canonical">``
  or ``og:url`` from a fetched page, which the extract stage uses to refine the
  canonical URL (#19).
* ``with_canonical_url`` builds the re-canonicalised article, because
  ``Article.id`` and ``canonical_url`` are frozen (ADR-0002).
"""

from __future__ import annotations

import contextlib
import re
from html.parser import HTMLParser
from urllib.parse import (
    SplitResult,
    parse_qsl,
    quote,
    quote_plus,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from newsfeed.config import DomainParamRules, UrlParamsConfig
from newsfeed.logging import get_logger
from newsfeed.models import Article

__all__ = [
    "TRACKING_PARAMS",
    "TRACKING_PREFIXES",
    "canonical_from_html",
    "normalise_url",
    "with_canonical_url",
]

log = get_logger(__name__)

TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        # Ad and social click ids
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "twclid",
        "ttclid",
        "li_fat_id",
        "igshid",
        "igsh",
        "mkt_tok",
        "_ga",
        "_gl",
        # Mailing lists and marketing automation
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "oly_anon_id",
        "oly_enc_id",
        "vero_id",
        "wt.mc_id",
        # Matomo / Piwik campaign parameters
        "pk_campaign",
        "pk_kwd",
        "pk_keyword",
        "pk_source",
        "pk_medium",
        "pk_content",
        "pk_cid",
        "mtm_campaign",
        "mtm_kwd",
        "mtm_keyword",
        "mtm_source",
        "mtm_medium",
        "mtm_content",
        "mtm_cid",
        "mtm_group",
        "mtm_placement",
        # Referral tags (required by #12) and other widely used campaign ids
        "ref",
        "ref_src",
        "cmpid",
        "ocid",
        "__twitter_impression",
    }
)
"""Query parameters (lowercase) that only track the click and never select content.

Policy: only vendor-defined, unambiguous tracking parameters belong here, because
these rules apply to every domain. Stripping a parameter that actually selects
content would merge two articles under one id and dedupe would silently drop
one, whereas missing a tracker only costs a duplicate (which the page's
``rel=canonical`` usually fixes at extract time). Publisher-specific parameters
(e.g. ``at_medium``, ``smid``, ``spm``) belong in per-domain config (#35).
"""

TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "hsa_")
"""Query parameter prefixes (lowercase) for vendor-defined families of tracking parameters.

Matomo/Piwik names are listed exactly in ``TRACKING_PARAMS`` rather than by
prefix, because sites use other ``pk_``/``mtm_`` names (e.g. ``pk_id``) for content.
"""

_AMP_QUERY_FLAGS = {"amp": {"", "1", "true"}, "outputtype": {"amp"}}
"""Query parameters that only switch a page to its AMP rendering."""

_DEFAULT_PORTS = {"http": 80, "https": 443}

_QUERY_ERRORS = "surrogateescape"

# Characters left unescaped in a path: RFC 3986 unreserved, sub-delims, ':' and '@',
# plus '/' and '%' so existing segments and escapes survive re-quoting.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_PCT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def normalise_url(
    url: str, *, resolve_amp: bool = True, rules: UrlParamsConfig | None = None
) -> str:
    """Return a normalised form of an absolute http(s) URL.

    Lowercases the scheme and host, IDNA-encodes the host, drops default ports,
    fragments and tracking parameters (``TRACKING_PARAMS``, ``TRACKING_PREFIXES``),
    sorts the remaining query parameters, normalises percent-escapes and dot
    segments, and strips trailing slashes except at the root. With
    ``resolve_amp`` (the default), common AMP URL patterns and AMP cache URLs
    are mapped to their non-AMP form.

    ``rules`` (loaded from ``config/url_params.yaml`` by the caller) adds
    per-domain parameter rules for the URL's host and its parent domains:
    ``strip`` and ``strip_prefixes`` remove more parameters, and ``keep``
    protects a parameter from every strip rule, global ones included. Without
    ``rules`` only the global lists apply. AMP query flags (``?amp=1``) are
    handled by ``resolve_amp``, not by ``keep``.

    Stripped parameter names are logged at debug level with ``url`` and
    ``stage``, so an unexpected merge of two articles can be traced.

    The result is stable: ``normalise_url(normalise_url(x)) == normalise_url(x)``.
    URLs with another scheme (``mailto:``, ``ftp:``) are returned stripped of
    surrounding whitespace but otherwise unchanged.

    Raises:
        ValueError: if ``url`` is not absolute (it has no scheme or no host).
    """
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme and scheme not in _DEFAULT_PORTS:
        return url
    if not scheme or not parts.hostname:
        raise ValueError(f"not an absolute URL: {url!r}")

    netloc = _normalise_netloc(scheme, parts)
    path = _normalise_path(parts.path)
    if resolve_amp:
        # Unwrap after normalising, and again after stripping AMP markers, so a port,
        # case, dot segments or a marker can't hide a cache URL on the first pass and
        # reveal it on the second (idempotence).
        unwrapped = _unwrap_amp_cache(netloc, path)
        if unwrapped is None:
            path = _strip_amp_path(path)
            unwrapped = _unwrap_amp_cache(netloc, path)
        if unwrapped is not None:
            query = f"?{parts.query}" if parts.query else ""
            return normalise_url(unwrapped + query, resolve_amp=resolve_amp, rules=rules)

    domain_rules = _rules_for_host(rules, netloc)
    # surrogateescape keeps non-UTF-8 escapes (e.g. Latin-1 "%E9") byte-for-byte.
    # The default, errors="replace", turns them all into U+FFFD, so distinct URLs
    # would share one id.
    params: list[tuple[str, str]] = []
    stripped: set[str] = set()
    for key, value in parse_qsl(parts.query, keep_blank_values=True, errors=_QUERY_ERRORS):
        if _is_tracking_param(key, domain_rules):
            stripped.add(key)
        else:
            params.append((key, value))
    if stripped:
        log.debug(
            "stripped query parameters",
            extra={"url": url, "stage": "ingest", "params": sorted(stripped)},
        )
    if resolve_amp:
        params = [(k, v) for k, v in params if not _is_amp_flag(k, v)]
    query = urlencode(sorted(params), quote_via=quote_plus, errors=_QUERY_ERRORS)
    return urlunsplit((scheme, netloc, path, query, ""))


def _normalise_netloc(scheme: str, parts: SplitResult) -> str:
    """Lowercase and IDNA-encode the host, keep userinfo, and drop a default port."""
    host: str = parts.hostname or ""
    try:
        port: int | None = parts.port
    except ValueError:  # non-numeric or out-of-range port: keep it verbatim
        port = None
        raw_port = parts.netloc.rsplit(":", 1)[-1]
    else:
        raw_port = str(port) if port is not None else ""
    host = host.rstrip(".")
    # An invalid IDNA label keeps the lowercased host rather than failing the URL.
    with contextlib.suppress(UnicodeError):
        host = host.encode("idna").decode("ascii")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    if raw_port and port != _DEFAULT_PORTS[scheme]:
        host = f"{host}:{raw_port}"
    userinfo = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else ""
    return f"{userinfo}@{host}" if userinfo else host


def _normalise_path(path: str) -> str:
    """Normalise escapes and dot segments, and strip trailing slashes except at the root."""
    path = quote(path, safe=_PATH_SAFE)  # escape raw non-ASCII and spaces
    path = _PCT_ESCAPE.sub(_normalise_escape, path)
    path = _remove_dot_segments(path or "/")
    stripped = path.rstrip("/")
    return stripped or "/"


def _normalise_escape(match: re.Match[str]) -> str:
    """Decode escaped unreserved characters and uppercase all other escapes."""
    char = chr(int(match.group(1), 16))
    return char if char in _UNRESERVED else f"%{match.group(1).upper()}"


def _remove_dot_segments(path: str) -> str:
    """Resolve ``.`` and ``..`` segments (RFC 3986 §5.2.4) in an absolute path."""
    output: list[str] = []
    segments = path.split("/")
    for segment in segments[1:]:
        if segment == ".":
            continue
        if segment == "..":
            if output:
                output.pop()
            continue
        output.append(segment)
    if segments[-1] in {".", ".."}:
        output.append("")  # "/a/b/.." resolves to "/a/", not "/a"
    return "/" + "/".join(output)


def _rules_for_host(rules: UrlParamsConfig | None, netloc: str) -> DomainParamRules | None:
    """Combine the rules for a normalised host and each of its parent domains.

    ``edition.example.com`` picks up the rules for ``edition.example.com`` and
    ``example.com``. Matching is by whole labels, so ``badexample.com`` doesn't
    match ``example.com``. Returns None if no rule applies.
    """
    if rules is None or not rules.domains:
        return None
    host = urlsplit(f"//{netloc}").hostname or ""
    labels = host.split(".")
    matches = [
        rule
        for i in range(len(labels) - 1)
        if (rule := rules.domains.get(".".join(labels[i:]))) is not None
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # model_construct: each rule is already validated, and a combination may
    # legitimately keep a name that another domain's rule strips.
    return DomainParamRules.model_construct(
        strip=frozenset().union(*(r.strip for r in matches)),
        strip_prefixes=tuple(p for r in matches for p in r.strip_prefixes),
        keep=frozenset().union(*(r.keep for r in matches)),
    )


def _is_tracking_param(key: str, domain_rules: DomainParamRules | None) -> bool:
    """Whether a query parameter is stripped: ``keep`` first, then every strip rule."""
    lowered = key.lower()
    if domain_rules is not None:
        if lowered in domain_rules.keep:
            return False
        if lowered in domain_rules.strip or lowered.startswith(domain_rules.strip_prefixes):
            return True
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def _is_amp_flag(key: str, value: str) -> bool:
    allowed = _AMP_QUERY_FLAGS.get(key.lower())
    return allowed is not None and value.lower() in allowed


def _unwrap_amp_cache(host: str, path: str) -> str | None:
    """Map an AMP cache URL to the publisher URL it serves, or return None.

    Handles the Google AMP cache (``<x>.cdn.ampproject.org/c/s/<host>/<path>``,
    where ``/s/`` means https and its absence http) and Google's AMP viewer
    (``www.google.com/amp/s/<host>/<path>``).
    """
    if host.endswith(".cdn.ampproject.org"):
        match = re.match(r"^/(?:c|v|i|r|wp)/(s/)?([^/]+\.[^/]+(?:/.*)?)$", path)
    elif host in {"www.google.com", "google.com"}:
        match = re.match(r"^/amp/(s/)?([^/]+\.[^/]+(?:/.*)?)$", path)
    else:
        return None
    if match is None:
        return None
    scheme = "https" if match.group(1) else "http"
    return f"{scheme}://{match.group(2)}"


def _strip_amp_path(path: str) -> str:
    """Remove AMP markers from a path: ``/amp`` segments and ``.amp`` suffixes.

    Covers ``/story/amp``, ``/amp/story`` and ``/story.amp`` (or
    ``/story.amp.html``), the patterns most publishers use for AMP pages.
    """
    # Repeat until stable so the result is idempotent (e.g. "/amp/amp/story").
    while True:
        segments = path.split("/")
        if len(segments) > 2 and segments[-1].lower() == "amp":
            segments = segments[:-1]
        if len(segments) > 2 and segments[1].lower() == "amp":
            segments = [segments[0], *segments[2:]]
        last = segments[-1]
        lowered = last.lower()
        if lowered.endswith(".amp") and len(last) > 4:
            segments[-1] = last[:-4]
        elif lowered.endswith(".amp.html") and len(last) > 9:
            segments[-1] = last[:-9] + ".html"
        stripped = "/".join(segments).rstrip("/") or "/"
        if stripped == path:
            return path
        path = stripped


class _CanonicalParser(HTMLParser):
    """Collect the first canonical link, og:url and base href from a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.canonical: list[str] = []
        self.og_url: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "").strip() for name, value in attrs}
        if tag == "base" and self.base_href is None and attributes.get("href"):
            self.base_href = attributes["href"]
        elif tag == "link":
            rel = attributes.get("rel", "").lower().split()
            if "canonical" in rel and attributes.get("href"):
                self.canonical.append(attributes["href"])
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key == "og:url" and attributes.get("content"):
                self.og_url.append(attributes["content"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def canonical_from_html(
    html: str, base_url: str, *, rules: UrlParamsConfig | None = None
) -> str | None:
    """Return the page's declared canonical URL, absolutised and normalised.

    Looks at ``<link rel="canonical">`` first, then ``<meta property="og:url">``,
    taking the first usable value of each. Relative values are resolved against
    the page's ``<base href>`` if present, otherwise ``base_url`` (the URL the
    page was fetched from). Values that don't resolve to an http(s) URL are
    ignored, as is a canonical pointing at the site root when the page itself is
    not the root (a common CMS misconfiguration that would merge every article
    on the site into one).

    A canonical on a different host is accepted, because syndicated articles
    legitimately point at the original, but it is logged with ``url``,
    ``canonical`` and ``stage="ingest"``. Returns None if no usable canonical is
    declared.

    ``rules`` are the per-domain parameter rules for ``normalise_url``. Pass the
    same rules used at ingest, so a refined id is derived the same way.

    Raises:
        ValueError: if ``base_url`` is not absolute (it has no scheme or no host).
    """
    parser = _CanonicalParser()
    parser.feed(html)
    parser.close()

    page = normalise_url(base_url, rules=rules)
    base = urljoin(base_url, parser.base_href) if parser.base_href else base_url
    for candidate in [*parser.canonical, *parser.og_url]:
        canonical = _resolve_candidate(candidate, base, page, rules)
        if canonical is None:
            continue
        if _site(canonical) != _site(page):
            log.info(
                "cross-domain canonical",
                extra={"url": base_url, "canonical": canonical, "stage": "ingest"},
            )
        return canonical
    return None


def _resolve_candidate(
    candidate: str, base: str, page: str, rules: UrlParamsConfig | None
) -> str | None:
    """Absolutise and normalise one declared canonical, or None if unusable."""
    absolute = urljoin(base, candidate)
    if urlsplit(absolute).scheme.lower() not in _DEFAULT_PORTS:
        return None
    try:
        canonical = normalise_url(absolute, rules=rules)
    except ValueError:
        return None
    if _is_site_root(canonical) and not _is_site_root(page):
        return None
    return canonical


def _is_site_root(url: str) -> bool:
    """Whether a normalised URL is the bare home page.

    The query counts: on ``/?p=123`` (WordPress plain permalinks) the query selects
    the article, so that page isn't the root and ``/?p=123`` is a valid canonical.
    """
    parts = urlsplit(url)
    return parts.path == "/" and not parts.query


def _site(url: str) -> str:
    """Host of a normalised URL without a leading ``www.``, for same-site checks."""
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def with_canonical_url(article: Article, canonical_url: str) -> Article:
    """Return a copy of ``article`` with a new canonical URL and the matching id.

    ``Article.id`` and ``canonical_url`` are frozen and ``model_copy`` skips
    validation (ADR-0002), so this rebuilds the article through
    ``model_validate``, which re-derives and checks the id. The caller is
    responsible for retiring the old id if it was already stored.
    """
    if canonical_url == article.canonical_url:
        return article
    data = article.model_dump()
    data.update(canonical_url=canonical_url, id=None)
    return Article.model_validate(data)
