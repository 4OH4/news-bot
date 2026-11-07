import DOMPurify from "dompurify";
import lists from "./allowed-tags.json";

// A second pass over HTML the backend already cleaned: the text is third-party input
// shown next to the owner's session, so the browser does not trust the first pass.
// The lists match extract/sanitize.py (checked by backend/tests/extract/test_client_tag_list.py).
const ALLOWED_TAGS: string[] = lists.tags;
const ALLOWED_ATTR: string[] = lists.attributes;

// DOMPurify lets data: image URLs past ALLOWED_URI_REGEXP, so the hook enforces the rule.
const HTTP = /^https?:/i;

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    if (!HTTP.test(node.getAttribute("href") ?? "")) node.removeAttribute("href");
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  } else if (node.tagName === "IMG") {
    if (!HTTP.test(node.getAttribute("src") ?? "")) node.removeAttribute("src");
    node.setAttribute("loading", "lazy");
    node.setAttribute("referrerpolicy", "no-referrer");
  }
});

/** Reduces article HTML to the reader's safe subset. Only http and https URLs survive. */
export function cleanArticleHtml(html: string): string {
  // Style blocks are cut before parsing: Chromium applies a parsed style block to the page and
  // the production CSP reports it, even though DOMPurify would then drop it.
  const withoutStyles = html.replace(/<style\b[\s\S]*?<\/style\s*>/gi, "");
  return DOMPurify.sanitize(withoutStyles, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOWED_URI_REGEXP: /^https?:/i,
    RETURN_TRUSTED_TYPE: false,
  });
}
