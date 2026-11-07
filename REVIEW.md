# What failure looks like here

Failure patterns taken from real defects in this repo. Test authors and workers read this before starting, and reviewers check every PR against it (`AUTOMATION.md` §7.3 and §7.4). When a review finds a new kind of defect, add a line here in a Tier B PR.

## Behaviour on real data

- A field a feature reads must be set by the real pipeline, shown by a test that runs it. In #123, a report section was always empty on real data, because only the unit tests set the field it read.
- A check on data must be written against what real sources send, not what the fixture happens to contain. Fixtures stay synthetic, but they should copy the structure and quirks of real pages and feeds.
- A feature that crosses stages needs one test through the real pipeline, with the real `Repository` and `BlobStore` adapters.
- A claim about what a later run will do needs a test across two runs. In #144, a design note promised a later merge that didn't exist.
- A parser of agents' comments or PR text must be tested against real comments from the repo, not a helper that writes the field it reads.

## Robust code

- Code that promises never to raise must handle malformed input, including non-ASCII digits and malformed URLs.
- One bad article or feed must not fail a run: failures are caught at the stage boundary and recorded on the item.
- A numeric range check must reject NaN and infinity.
- A regex that reads one line must not use `\W` or `\s`, which match a newline: an empty field would take its text from the next line. In #133 an empty `Reason:` passed the merge gate this way. Use `[^\w\n]` and `[^\S\n]`.
- A retry loop or single-flight lock (one holder at a time) must limit how long one attempt can wait.
- An empty environment variable that holds a path becomes `Path('.')`, the current directory. Treat empty as unset.

## Polite fetching and safety

- Every hop of a redirect must pass the robots.txt check.
- A destructive operation must be limited to files the code created.
- A rule protecting stored state must cover every path that writes it, including earlier steps that re-key or delete.
- A batch article is a snapshot, so a write must not overwrite or delete a row another article changed earlier in the run.
- Don't replace stored state on a result not yet known.
- A write deferred to protect stored state must still be durable: holding it in memory trades a race for a loss when the page closes.
- Nothing may bypass a paywall or an access control, and third-party article text must not reach the public repo.
- For an XSS-style risk (script injected through content), check whether it can persist through browser storage, such as the service-worker cache or IndexedDB, and so outlive the fix.
- Build output can contain symlinks: copy and serve code must not follow them out of the tree.
- A privileged deploy must not consume a cache, or any other file, that a less-trusted step could write. The instance: build code wrote the file the deploy then read (PR #186, defect 2 of its second review).
- A stage that runs third-party build code must be checked for `RUN --mount` and `ADD` as well as `COPY`, since each can bring in files from outside the build context.

## Cross-issue checks

- A criterion in one issue must be checked against the mounts, scopes and gates that an earlier issue set up. The instance: PR #186, defect 2 of its first review (a drift test that the earlier issue's mounts put out of reach).
- A limit claimed for an account must count what it can do by acting as another account, and what each client library does with its credentials.
- Renumbering (ADR or issue numbers) must be checked against what is already merged, so no number is reused or a reference left pointing at the old one.
- A cost claim needs a per-request estimate, not only a monthly one, because a monthly total hides a request that is costly on its own.
- A Terraform map or variable holding sensitive values must be marked `sensitive`, or the values show in plan output.

## Rules that classify changes

- A rule must match the whole class of change, not one example path or line. In #133, only the top-level `conftest.py` was covered, though each tool takes the nearest configuration file, and IAM was matched on changed lines, so a one-line `member` change inside an IAM resource passed as routine. Ask what else does the same job under another name or in another place.
- A new item in a first-match list (rules checked in order, the first hit wins) must be checked against the items above it, because one of them may already match.

## Agent briefs

- A new CI check counts only if the merge gate reads it.
- A command in an agent brief must be checked under the isolation that agent runs with. In #133, briefs ran git against a sibling worktree, by `git -C` and by `cd`, which the harness refuses for an isolated agent, so the hand-off between agents could not work.
- A step that reads something later must name where it is stored, because agent reports and lead sessions don't persist. In #138, the milestone review read friction notes that existed in no file, issue or comment.
- A split PR's body must not carry a closing keyword (such as `Closes`) for an issue that a later part closes.
- A trailer that a gate reads must sit in the last paragraph of the commit message.

## Tests that can fail

- A test must fail when the behaviour it covers is broken. Break the code and watch it fail.
- A test must check the outcome a user would see, not only that a function was called.
- A deleted or loosened assertion needs a reason in the PR.
- A test for a guard must reach the guard's line.
- A test for a false match uses the real input that showed it, not a tidied-up copy.
- A test that one async step finishes before the next must use a step that resolves later, or dropping an `await` goes unnoticed.
- A behaviour that depends on a browser value set by a header must be tested through the control the user actually uses, under the production headers.
- A time-relative test must fix "now" on both sides, the code under test and the data it compares against.
- When a rework changes which function a hook registers, the tests must register the new one too, or they keep exercising the old path.
- A flaky-test fix must first show the failure it fixes (a reproduction or a captured failing run) before it claims a cause.
- In the client every screen is a full page load, so a component's unmount cleanup never runs when the owner leaves. Work that must survive leaving (a reading-time event, a feedback write) must finish before navigating, be written synchronously on `pagehide` or hide, or be handed to the next page. Test it by navigating away in Playwright, not by unmounting. Instances: PR #248, PR #251.

## Workflows and exemptions

- A job or step `if` meant to run after a failure must contain a status function (`failure()`, `always()`), or it is skipped as soon as something fails. A workflow path that was never exercised is unverified.
- An exemption that needs both sides of a word (such as a backtick on each side) can't be written as two negative lookarounds, because each one alone still lets a half-quoted word through. Remove the whole exempt form first, then search what is left.
- Moving a section changes where a parser finds its end: test with the real template's shape, not a shortened body.
