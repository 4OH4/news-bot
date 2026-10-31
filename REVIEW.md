# What failure looks like here

Failure patterns taken from real defects in this repo. Test authors and workers read this before starting, and reviewers check every PR against it (`AUTOMATION.md` §7.3 and §7.4). When a review finds a new kind of defect, add a line here in a Tier B PR.

## Behaviour on real data

- A field a feature reads must be set by the real pipeline, shown by a test that runs it. In #123, a report section was always empty on real data, because only the unit tests set the field it read.
- A check on data must be written against what real sources send, not what the fixture happens to contain. Fixtures stay synthetic, but they should copy the structure and quirks of real pages and feeds.
- A feature that crosses stages needs one test through the real pipeline, with the real `Repository` and `BlobStore` adapters.

## Robust code

- Code that promises never to raise must handle malformed input, including non-ASCII digits and malformed URLs.
- One bad article or feed must not fail a run: failures are caught at the stage boundary and recorded on the item.

## Polite fetching and safety

- Every hop of a redirect must pass the robots.txt check.
- A destructive operation must be limited to files the code created.
- Nothing may bypass a paywall or an access control, and third-party article text must not reach the public repo.

## Tests that can fail

- A test must fail when the behaviour it covers is broken. Break the code and watch it fail.
- A test must check the outcome a user would see, not only that a function was called.
- A deleted or loosened assertion needs a reason in the PR.
