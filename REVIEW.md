# What failure looks like here

Failure patterns taken from real defects in this repo. Test authors and workers read this before starting, and reviewers check every PR against it (`AUTOMATION.md` §7.3 and §7.4). When a review finds a new kind of defect, add a line here in a Tier B PR.

## Behaviour on real data

- A field a feature reads must be set by the real pipeline, shown by a test that runs it. In #123, a report section was always empty on real data, because only the unit tests set the field it read.
- A check on data must be written against what real sources send, not what the fixture happens to contain. Fixtures stay synthetic, but they should copy the structure and quirks of real pages and feeds.
- A feature that crosses stages needs one test through the real pipeline, with the real `Repository` and `BlobStore` adapters.

## Robust code

- Code that promises never to raise must handle malformed input, including non-ASCII digits and malformed URLs.
- One bad article or feed must not fail a run: failures are caught at the stage boundary and recorded on the item.
- A regex that reads one line must not use `\W` or `\s`, which match a newline: an empty field would take its text from the next line. In #133 an empty `Reason:` passed the merge gate this way. Use `[^\w\n]` and `[^\S\n]`.

## Polite fetching and safety

- Every hop of a redirect must pass the robots.txt check.
- A destructive operation must be limited to files the code created.
- Nothing may bypass a paywall or an access control, and third-party article text must not reach the public repo.

## Rules that classify changes

- A rule must match the whole class of change, not one example path or line. In #133, only the top-level `conftest.py` was covered, though each tool takes the nearest configuration file, and IAM was matched on changed lines, so a one-line `member` change inside an IAM resource passed as routine. Ask what else does the same job under another name or in another place.

## Agent briefs

- A command in an agent brief must be checked under the isolation that agent runs with. In #133, briefs ran git against a sibling worktree, by `git -C` and by `cd`, which the harness refuses for an isolated agent, so the hand-off between agents could not work.
- A step that reads something later must name where it is stored, because agent reports and lead sessions don't persist. In #138, the milestone review read friction notes that existed in no file, issue or comment.

## Tests that can fail

- A test must fail when the behaviour it covers is broken. Break the code and watch it fail.
- A test must check the outcome a user would see, not only that a function was called.
- A deleted or loosened assertion needs a reason in the PR.
