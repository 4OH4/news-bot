"""Tests for ``newsfeed.extract.sanitize``."""

from __future__ import annotations

import logging
import re

import pytest

from newsfeed.extract import sanitize
from newsfeed.extract.sanitize import ALLOWED_TAGS, html_to_text, sanitize_html

BASE = "https://news.example.com/section/story.html"


def clean(html: str, **kwargs: str) -> str:
    return sanitize_html(html, BASE, **kwargs)


# --- Dangerous content -------------------------------------------------------


@pytest.mark.parametrize(
    "markup",
    [
        "<script>alert('x')</script>",
        "<script src='https://evil.example.net/x.js'></script>",
        "<style>p { color: red }</style>",
        "<iframe src='https://video.example.org/embed/1'>fallback text</iframe>",
        "<noscript>Enable JavaScript</noscript>",
        "<object data='x.swf'>object text</object>",
        "<svg><text>svg text</text></svg>",
        "<button onclick='go()'>Accept all</button>",
        "<template><p>template text</p></template>",
    ],
)
def test_removes_dangerous_elements_with_their_content(markup: str) -> None:
    out = clean(f"<p>Kept text.</p>{markup}")
    assert out == "<p>Kept text.</p>"


@pytest.mark.parametrize(
    "markup",
    [
        '<p onclick="steal()">Text</p>',
        '<p onmouseover="steal()" ONLOAD="steal()">Text</p>',
        '<p><img src="/a.jpg" onerror="steal()" alt="A"> Text</p>',
        '<p><a href="/x" onfocus="steal()">Text</a></p>',
    ],
)
def test_removes_event_handler_attributes(markup: str) -> None:
    out = clean(markup)
    assert "steal" not in out
    assert "on" not in out.replace("noopener", "")


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        " javascript:alert(1)",
        "java&#x09;script:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,<script>alert(1)</script>",
    ],
)
def test_unwraps_links_with_unsafe_schemes(href: str) -> None:
    out = clean(f'<p>Read <a href="{href}">this</a> now.</p>')
    assert out == "<p>Read this now.</p>"


@pytest.mark.parametrize(
    "src",
    [
        "javascript:alert(1)",
        "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
        "",
    ],
)
def test_drops_images_without_a_safe_source(src: str) -> None:
    out = clean(f'<p>Before</p><p><img src="{src}" alt="pixel"></p><p>After</p>')
    assert out == "<p>Before</p><p>After</p>"


def test_strips_attributes_outside_the_allowlist() -> None:
    out = clean(
        '<p class="lead" id="p1" style="color:red" data-track="1">Text '
        '<a href="/x" target="_blank" title="t" rel="nofollow">link</a></p>'
    )
    assert out == (
        '<p>Text <a href="https://news.example.com/x" rel="noopener noreferrer">link</a></p>'
    )


def test_removes_comments() -> None:
    assert clean("<p>Text<!-- secret --></p>") == "<p>Text</p>"


def test_title_element_content_is_removed() -> None:
    out = clean("<html><head><title>Site name</title></head><body><p>Body</p></body></html>")
    assert out == "<p>Body</p>"


# --- Allowlist ---------------------------------------------------------------


def test_keeps_allowed_structure() -> None:
    html = (
        "<h2>Heading</h2><h3>Sub</h3><h4>Minor</h4>"
        "<p><em>em</em> <strong>strong</strong> <code>code</code><br>line</p>"
        "<ul><li>one</li></ul><ol><li>two</li></ol>"
        "<blockquote><p>quote</p></blockquote>"
        "<pre>pre text</pre><hr>"
        '<figure><img src="https://img.example.com/a.jpg" alt="A"><figcaption>Cap</figcaption>'
        "</figure>"
        "<table><thead><tr><th>H</th></tr></thead><tbody><tr><td>D</td></tr></tbody></table>"
    )
    assert clean(html) == html


def test_unwraps_disallowed_inline_tags_keeping_text() -> None:
    out = clean('<p>A <span class="x">B</span> <font color="red">C</font> <u>D</u></p>')
    assert out == "<p>A B C D</p>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<h1>Top</h1>", "<h2>Top</h2>"),
        ("<h5>Small</h5>", "<h4>Small</h4>"),
        ("<h6>Tiny</h6>", "<h4>Tiny</h4>"),
        ("<p><b>bold</b> <i>italic</i></p>", "<p><strong>bold</strong> <em>italic</em></p>"),
    ],
)
def test_renames_equivalent_tags(markup: str, expected: str) -> None:
    assert clean(markup) == expected


def test_layout_containers_become_paragraphs_or_are_unwrapped() -> None:
    out = clean(
        "<div>First block.</div><div>Second block.</div>"
        "<section><div><p>Inner para.</p></div><div>Loose text.</div></section>"
    )
    assert out == "<p>First block.</p><p>Second block.</p><p>Inner para.</p><p>Loose text.</p>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<dl><dt>Term</dt><dd>Definition</dd></dl>", "<p>Term</p><p>Definition</p>"),
        (
            "<dl><dt>Term</dt><dd>Lead <em>in</em><ul><li>item</li></ul>after</dd></dl>",
            "<p>Term</p><p>Lead <em>in</em></p><ul><li>item</li></ul><p>after</p>",
        ),
        (
            "<details><summary>More</summary>Hidden text<p>Para</p>tail</details>",
            "<p>More</p><p>Hidden text</p><p>Para</p><p>tail</p>",
        ),
        ("<nav>Home</nav><address>1 Invented Road</address>", "<p>Home</p><p>1 Invented Road</p>"),
        (
            "<div>x<p>y</p>z</div><div>w<p>v</p></div>",
            "<p>x</p><p>y</p><p>z</p><p>w</p><p>v</p>",
        ),
    ],
)
def test_block_containers_keep_block_boundaries(markup: str, expected: str) -> None:
    assert clean(markup) == expected


def test_table_caption_keeps_a_block_boundary() -> None:
    out = clean("<table><caption>Results</caption><tr><td>Row</td></tr></table>")
    # The caption becomes a paragraph; the HTML parser moves it ahead of the table.
    assert out == "<p>Results</p><table><tbody><tr><td>Row</td></tr></tbody></table>"
    assert html_to_text(out) == "Results\nRow"


def test_nested_pre_becomes_pre_code() -> None:
    assert clean("<pre><pre>x = 1</pre></pre>") == "<pre><code>x = 1</code></pre>"


def test_output_tags_are_all_allowed() -> None:
    out = clean(
        "<article><header><h1>T</h1></header><div><p>Body <span>x</span></p>"
        "<aside><dl><dt>a</dt></dl></aside></div><footer>F</footer></article>"
    )
    tags = set(re.findall(r"<([a-z0-9]+)", out))
    assert tags
    assert tags <= ALLOWED_TAGS


# --- URLs --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/about", "https://news.example.com/about"),
        ("other.html", "https://news.example.com/section/other.html"),
        ("../up.html", "https://news.example.com/up.html"),
        ("//cdn.example.com/x", "https://cdn.example.com/x"),
        ("#notes", "https://news.example.com/section/story.html#notes"),
        ("https://elsewhere.example.org/p", "https://elsewhere.example.org/p"),
        ("mailto:editor@example.com", "mailto:editor@example.com"),
    ],
)
def test_links_are_absolutised(href: str, expected: str) -> None:
    out = clean(f'<p><a href="{href}">link</a></p>')
    assert out == f'<p><a href="{expected}" rel="noopener noreferrer">link</a></p>'


def test_image_src_is_absolutised_and_srcset_dropped() -> None:
    out = clean(
        '<p><img src="/img/a.jpg" srcset="/img/a-2x.jpg 2x" sizes="100vw" alt="A"'
        ' width="10" loading="lazy"></p>'
    )
    assert out == '<p><img src="https://news.example.com/img/a.jpg" alt="A"></p>'


@pytest.mark.parametrize("attr", ["data-src", "data-lazy-src", "data-original"])
def test_lazy_load_attribute_replaces_placeholder_src(attr: str) -> None:
    out = clean(
        f'<p><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" {attr}="/real.jpg"'
        ' data-srcset="/real-640.jpg 640w" alt="Real"></p>'
    )
    assert out == '<p><img src="https://news.example.com/real.jpg" alt="Real"></p>'


def test_lazy_load_attribute_wins_over_placeholder_http_src() -> None:
    out = clean('<p><img src="/placeholder.png" data-src="https://img.example.org/r.jpg"></p>')
    assert out == '<p><img src="https://img.example.org/r.jpg"></p>'


def test_unsafe_lazy_source_falls_back_to_src() -> None:
    out = clean('<p><img src="/fallback.jpg" data-src="javascript:x()"></p>')
    assert out == '<p><img src="https://news.example.com/fallback.jpg"></p>'


# --- Empty elements ----------------------------------------------------------


@pytest.mark.parametrize(
    "markup",
    [
        "<p></p>",
        "<p>   </p>",
        "<p>&nbsp;</p>",
        "<p><br></p>",
        "<p><span> </span><em></em></p>",
        "<ul><li></li><li> </li></ul>",
        '<p><a href="/x"></a></p>',
        "<blockquote><p></p></blockquote>",
        "<figure><figcaption></figcaption></figure>",
        "<table><tr><td></td></tr></table>",
        "<div><div></div></div>",
    ],
)
def test_removes_empty_elements(markup: str) -> None:
    assert clean(f"<p>Text</p>{markup}") == "<p>Text</p>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<p>foo<em> </em>bar</p>", "<p>foo bar</p>"),
        ("<p>foo<strong>\n</strong>bar</p>", "<p>foo\nbar</p>"),
        ("<p>foo<span> <b> </b></span>bar</p>", "<p>foo  bar</p>"),
        ('<p>foo<a href="/x"> </a>bar</p>', "<p>foo bar</p>"),
        ("<p>foo<code>&nbsp;</code>bar</p>", "<p>foo&nbsp;bar</p>"),
        ("foo<p> </p>bar", "foo bar"),
    ],
)
def test_removing_whitespace_only_elements_keeps_words_apart(markup: str, expected: str) -> None:
    out = clean(markup)
    assert out == expected
    assert "foobar" not in html_to_text(out)
    assert html_to_text(out).split() == ["foo", "bar"]


def test_keeps_elements_whose_only_content_is_an_image() -> None:
    html = (
        '<p><a href="https://news.example.com/big.jpg">'
        '<img src="https://news.example.com/small.jpg" alt=""></a></p>'
    )
    out = clean(html)
    assert out == (
        '<p><a href="https://news.example.com/big.jpg" rel="noopener noreferrer">'
        '<img src="https://news.example.com/small.jpg" alt=""></a></p>'
    )


def test_keeps_empty_cells_in_a_row_with_content() -> None:
    html = "<table><tbody><tr><td>A</td><td></td></tr></tbody></table>"
    assert clean(html) == html


def test_text_tails_survive_element_removal() -> None:
    assert clean("<p>Before <span></span>after</p>") == "<p>Before after</p>"
    assert clean('<p>See <img src="javascript:x"> here</p>') == "<p>See  here</p>"


# --- Leading title -----------------------------------------------------------


def test_drops_leading_heading_that_repeats_the_title() -> None:
    out = clean("<h1>The  Big Story</h1><p>Body.</p>", title="the big story")
    assert out == "<p>Body.</p>"


def test_keeps_leading_heading_that_differs_from_the_title() -> None:
    out = clean("<h2>Background</h2><p>Body.</p>", title="The big story")
    assert out == "<h2>Background</h2><p>Body.</p>"


def test_keeps_title_heading_that_is_not_first() -> None:
    out = clean("<p>Intro.</p><h2>The big story</h2>", title="The big story")
    assert out == "<p>Intro.</p><h2>The big story</h2>"


# --- General -----------------------------------------------------------------


@pytest.mark.parametrize("html", ["", "   ", "<script>x</script>", "<p></p>"])
def test_empty_results(html: str) -> None:
    assert clean(html) == ""


def test_escapes_text_and_preserves_entities() -> None:
    out = clean("Loose &lt;b&gt; text<p>Fish &amp; chips &lt;script&gt;</p>")
    assert out == "Loose &lt;b&gt; text<p>Fish &amp; chips &lt;script&gt;</p>"


@pytest.mark.parametrize("depth", [300, 1000])
def test_deeply_nested_markup_keeps_its_text(
    depth: int, log_records: list[logging.LogRecord]
) -> None:
    blocks = "<div>" * depth + "<p>Deep paragraph text.</p>" + "</div>" * depth
    inline = "<span>" * depth + "Deep inline text." + "</span>" * depth
    assert clean(blocks) == "<p>Deep paragraph text.</p>"
    assert clean(f"<p>{inline}</p>") == "<p>Deep inline text.</p>"
    assert html_to_text(blocks) == "Deep paragraph text."
    assert not [r for r in log_records if r.levelno >= logging.WARNING]


def test_warns_when_text_is_lost(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    # Simulate a parser limit that drops everything.
    monkeypatch.setattr(sanitize, "_normalise", lambda *args: "")
    assert clean("<p>Some &amp; text</p>") == ""
    warnings = [r for r in log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    record = vars(warnings[0])
    assert record["stage"] == "extract"
    assert record["url"] == BASE
    assert record["error_type"] == "SanitiserTextLost"
    assert record["error"]
    assert record["input_chars"] == len("<p>Some &amp; text</p>")


@pytest.mark.parametrize("html", ["<p>&nbsp;</p>", "<script>x()</script>", "<p><br></p>"])
def test_no_warning_when_input_had_no_text(html: str, log_records: list[logging.LogRecord]) -> None:
    assert clean(html) == ""
    assert not log_records


def test_is_idempotent() -> None:
    html = (
        '<div class="x"><h1>T</h1><p onclick="x()">A <a href="/l">l</a></p>'
        '<img data-src="/i.jpg" src="data:,"><script>s()</script></div>'
    )
    once = clean(html)
    assert clean(once) == once


# --- html_to_text ------------------------------------------------------------


def test_html_to_text_separates_blocks_and_collapses_spaces() -> None:
    html = (
        "<h2>Head</h2><p>One  <em>two</em>\n three</p><ul><li>four</li><li>five</li></ul>"
        "<p>six<br>seven</p>"
    )
    assert html_to_text(html) == "Head\nOne two three\nfour\nfive\nsix\nseven"


def test_html_to_text_ignores_image_alt_text() -> None:
    assert html_to_text('<p>Words <img src="https://x.example/a.jpg" alt="alt words"></p>') == (
        "Words"
    )


@pytest.mark.parametrize("html", ["<p></p>", "Loose text first<h2>Title</h2>"])
def test_title_check_handles_empty_or_text_first_content(html: str) -> None:
    assert clean(html, title="Title") == clean(html)


@pytest.mark.parametrize("html", ["", "  ", "<p></p>"])
def test_html_to_text_empty(html: str) -> None:
    assert html_to_text(html) == ""
