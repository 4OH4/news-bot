# Automation: how news-bot is built

This document explains the workflow, the agents, the method and the decisions behind them. `PROJECT_PLAN.md` says what is being built. `CLAUDE.md` holds the day-to-day rules and commands, and `.claude/agents/` and `.claude/skills/` hold the working briefs.

**The goal is to deliver high-quality software at high velocity, with minimal un-planned human oversight. Human code review can become a bottleneck, so this framework is intended to allow AI agents to continue working when possible, using a risk-managed based approach to decide when to make a decision themselves and when to escale for human input. The bar is the 'cost of failure', and varies by project and lifecycle - the cost is different during development vs in production.**

This document describes what we expect to happen, and why. It is not the implementation: the briefs, skills and scripts are, and they are derived from it. No agent needs it in context to do its job, so each brief and skill must be complete by itself. A change to a rule here goes into the matching brief, skill or script in the same PR.

## 1. The model in brief

The human sets the intent and reviews working software at the end of each phase. Agents do everything in between.

```
Human: agrees the phase goal
  │
  ▼
GEAR 1: are we building the right thing?
  Lead      owns delivery of the phase, its goals and fit with later phases
  Architect turns the phase into a design, issues and acceptance examples
  │
  ▼
GEAR 2: is it built right? (one batch of issues at a time)
  Delivery manager runs a batch of issues, and for each one:
  Test author → Worker → CI → Reviewer → merge through tier.py
  │                                 │
  │    design problems go back up to Gear 1
  ▼
Milestone review: the human uses a working demo and confirms the decisions
```

The aim is a phase that runs from start to finish with no human input. Agents make reversible decisions themselves and record them for the milestone review. Work waits for the human only in the cases listed in §6.

## 2. Why it works this way

- **Human review was the bottleneck.** Six PRs waited 11 days in October 2026. Of 25 tiered PRs, only 3 were eligible for the lead to merge. The Tier B PRs were held back by path rules: all 12 touched files outside the Tier A list, and only one was also over a size limit.
- **Most decisions are cheap to change.** This is a single-user product, so a wrong choice in ranking, a source list or a data model can usually be corrected later. Waiting for approval costs more than an occasional fix.
- **Passing tests aren't enough.** Reviewers found more than 40 distinct defects in work that had already passed its tests. In one, a report section was always empty on real data, because only the unit tests set the field it read (#123). Others included redirects that bypassed robots.txt, and tests that could not fail.
- **Some mistakes can't be reverted.** Harm to third-party sites, a bill, a leaked secret or deleted data stays with the human, however good the automation becomes.

## 3. Principles

1. Milestones are the planned human touch point. Everything else is the exception.
2. Decide and record instead of asking, when a decision is reversible.
3. Review is the acceptance gate. Test results are evidence for it, and do not replace it.
4. Tests come first, written by a different agent from the code.
5. The tier comes from the diff, so nobody's judgement decides their own level of scrutiny.
6. Agents can't loosen their own limits.
7. Agents do all the preparation. The human never edits files, ticks boxes or tidies PRs.
8. The steps in this process are defaults, not barriers. The lead uses judgement and can propose skipping or reordering one, the human agrees in the chat, and the lead records it in one line in the Phase Log. Stop-the-line items, the tiers, the architecture rules and merging only through `tier.py` are outside this.

## 4. Phases, epics and milestones

**Phases** are the ones in `PROJECT_PLAN.md` §6, and each maps to a GitHub milestone. A phase is a substantial piece of work holding several features. It must end with something runnable that the human can use.

**Epics** are optional. The architect uses one when a phase has several distinct features, usually more than about ten issues, and each epic should be demo-able by itself. An epic is a parent issue with a checklist of its child issues. It adds no human touch point.

Each phase runs through these stages:

1. **Agree the goal.** The goal and exit criterion come from `PROJECT_PLAN.md`, confirmed at the previous milestone review.
2. **Design.** The architect writes the phase design and issues (§5.2). The lead checks them against the project goals and later phases.
3. **Build.** Issues run through Gear 2 (§8) in dependency order.
4. **Prepare the milestone.** Once the phase's work is done, or the human has agreed what carries forward, the lead runs `/lead-milestone`: it runs as much of the demo as it can from a clean checkout, then writes the milestone report. The report says which demo steps are untested, and the human's own run tests them.
5. **Milestone review.** The human uses the demo, reviews the decisions, and comments. Agents make any requested changes.
6. **Sign-off.** The human merges the milestone report PR. Declining or commenting asks for changes, as with any other PR.

### The milestone report

The report lives at `docs/milestones/phase-<n>.md`, following the pattern of `docs/phase1-review.md`, and it should take a few minutes to read:

- **Try it:** the exact command or URL, and what to do with it.
- **Goal check:** the exit criterion and the evidence that it is met.
- **Decisions to confirm:** every `Confirm later` item, with its reason and how to undo it.
- **What was built:** a short summary, with links to the PRs that agents merged.
- **Deferred work:** each entry in `DEFERRED_WORK.md` that is due for a look gets address, accept or stays deferred, and resolved entries are removed.
- **Risks and parked work:** anything unresolved, any PR the review process could not clear, and anything carried forward to the next phase.
- **Process measures:** the numbers in §10.
- **Process review:** the §10 numbers against the previous milestone, a status table giving every friction item and proposal, and every follow-up with no issue, a status with its evidence: address (the recommended change and the measure that shows it worked), accept (the reason; items sharing one are grouped) or defer (an entry in `DEFERRED_WORK.md` saying why and when to look again). A follow-up with an issue carries forward as that issue, a dropped follow-up is accepted, and an undecided one needs a status. Addressed items past the three-issue limit (§10) go to `DEFERRED_WORK.md`. The review also covers how the last milestone's changes turned out, and the human's own friction notes.
- **Next phase:** proposed goal, and anything found this phase that changes the plan.
- **Cost of failure:** the lead's statement for the next phase, and what changed (§6). Merging the report confirms it.

Sign-off is always a positive confirmation from the human. Nothing moves on automatically, and no time limit or waiting period applies to a milestone review. Where an exit criterion mentions elapsed time, such as "runs hourly for a week", the report gives the evidence so far, and the human judges when it's enough. Fixes during the review go through Gear 2 as normal.

The report gets an independent review before it reaches the human, and its PR also updates the phase's status in `PROJECT_PLAN.md` §6, marking the phase **done**. A rejected decision becomes an issue in the next phase, and it doesn't block sign-off unless the human says so. After sign-off, the lead closes the GitHub milestone and moves carried items to the next phase. Only process fixes that the next phase relies on go first, and the rest carry forward.

**Feedback during a phase.** The human can try work in progress at any time and comment in the chat or the Phase Log. The lead turns each comment into an issue or a follow-up.

### The Phase Log

The Phase Log is one GitHub issue, titled "Phase N log", that the lead keeps throughout a phase. The human can read it at any time to see where the phase stands, and the milestone report is written from it. It is an issue, not a file, because a file would need a PR for every update and would conflict across parallel PRs.

The issue body is the curated summary, and only the lead rewrites it. Comments hold raw notes, including the human's. The body has these sections:

- **What's new:** one plain line per merged change, saying what the human would see or try.
- **Decisions to confirm:** every `Confirm later` item, with its reason and how to undo it, plus "Revisit when" if a later event should reopen it.
- **Friction:** merged and counted across batches, not listed batch by batch.
- **Proposed process changes:** each with the friction behind it.
- **Follow-ups:** anything found and not acted on, marked drop, an issue number, carried forward, or undecided. Nothing stays undecided past the milestone.

The rules:

- **Write it when it happens.** Anything the lead tells the human will be recorded goes into a comment at that moment, as do the lead's own observations and any correction from the human. The handoff note holds session state only, because each session overwrites it.
- **Curate at each batch close.** The lead merges the batch report and the comments into the body.
- **Reconcile before finishing.** `/lead-finish` checks the session's reports against the log before it replaces the handoff note.
- **The milestone report closes it.** Its "Decisions to confirm", "Risks and parked work" and "Process review" come from the log.

## 5. The agents

| Role | Gear | Runs as | Model | Can change code? |
|---|---|---|---|---|
| Lead | 1 | Main session | Opus | Only generated evaluation results, the milestone report, the phase's status line in `PROJECT_PLAN.md`, §15, `DEFERRED_WORK.md` and the cost-of-failure statement in `CLAUDE.md` |
| Architect | 1 | Subagent, fresh per phase or epic | Opus | The phase design and issues only |
| Delivery manager | 2 | Background subagent, fresh per batch | Sonnet, Opus if that falls short | No, except merging through `tier.py` |
| Test author | 2 | Subagent, one per issue | Sonnet, Opus for high-risk issues | Tests only |
| Worker | 2 | Subagent, one per issue | Sonnet, Opus for design-heavy issues | Yes, on its own branch |
| Reviewer | 2 | Subagent, fresh per PR | Opus | No, read-only |

The lead starts the architect and the delivery manager. The delivery manager starts the test authors, workers and reviewers, with the scripts doing the routine mechanics. This nesting depends on the pilot in §12.

### 5.1 Lead

The lead is accountable for delivering the phase. It:

- keeps the work aligned with the project's goals and intent;
- checks that what is built will suit later phases;
- makes the decisions that reach it from Gear 2 or calls in the architect;
- groups Ready issues into batches and runs them one at a time, with a delivery manager for each;
- reviews each batch report;
- handles usage pauses (§11);
- runs `make eval` when a delivery manager asks, because subagents can't run it;
- keeps the Phase Log (§4) and the cost-of-failure statement (§6), and writes the milestone report.

To protect its context, the lead reads batch reports, not diffs or per-agent results. It sends agents issue numbers instead of restating them. It restarts between sessions as it does today, with `/lead-start`, `/lead-finish` and a handoff note.

### 5.2 Architect

The architect is a fresh subagent, which keeps long planning work out of the lead's context. It reads the plan, the ADRs, the code and the last milestone report, or that phase's log if the report isn't merged yet, then produces:

- **a phase design** at `docs/phases/phase-<n>.md`: the approach, the demo, any epics, the issue graph, expected contract changes and ADRs, a check against later phases, what the human must do and what agents need first, and the first point the human can use something;
- **issues** that follow `.github/ISSUE_TEMPLATE/`, with acceptance examples, scope, interfaces and dependencies;
- **a risk flag** on any issue that needs Opus.

An acceptance example is a concrete given/when/then case in plain language. For example: "Given a feed with one failed article, when the pipeline runs and `newsfeed report` is called, then that article appears under failed articles." Examples describe behaviour. Interfaces are named in the issue, so the test author and worker can work independently.

The phase design goes in as the phase's first PR. The lead checks it, then it takes the normal route without a test author: an independent reviewer, then a delivery manager merges it. It can merge before the previous phase's sign-off, because only building waits for that. The architect updates the file through the same route when the design changes mid-phase, and the lead can call it back whenever a design problem emerges.

### 5.3 Delivery manager

The delivery manager is Gear 2's orchestrator. A fresh one runs each batch of about 5 to 8 issues, so its context stays small. A large epic takes several batches. It:

- starts test authors, workers and reviewers, within the limits in §11, using the architect's risk flags to choose models;
- runs `board.py promote` after each merge and keeps the cards up to date;
- routes defects back for rework;
- paces work against the usage limit (§11);
- re-runs the checks, posts the "Delivery review" and merges through `tier.py`;
- sends design problems and stop-the-line items to the lead as they arise, and carries on with other issues;
- sends any new `REVIEW.md` lines to a worker as a small docs PR before it reports (§7.4);
- finishes with one report: what merged, what's parked, any `Confirm later` items and the reviewers' notes.

It can't wait in place for background agents, so it hands back a short status while they run, and their notifications resume it. Only its last message is the batch report. All its state is on GitHub and the board. If a batch stops, a new delivery manager picks up from there.

### 5.4 Test author

The test author works from the issue and the existing code, before any fix or implementation exists. It sets the card to In progress and creates `issue-<n>-<slug>` from up-to-date `origin/main`. It writes tests for the acceptance examples, including one test through the real pipeline for any feature that crosses stages. It confirms the tests fail, commits them to the issue branch as `test:` with a `Test-Author:` trailer, which is how the test change report tells its commits from a worker's, pushes and reports its branch, SHA and worktree path. CI doesn't run on a branch without a PR, so red commits don't affect it.

### 5.5 Worker

The delivery manager frees the test author's worktree with `git worktree remove`, as a branch can only be checked out in one, and the worker takes the same branch. It can't inspect that worktree, because isolated agents are refused git commands aimed at another worktree, so it relies on git refusing a dirty tree and on the SHA in the test author's report. Where an issue has no test author (§7.2), the worker creates the branch and sets the card. The worker implements the issue until the tests pass, and rebases on `main` before opening the PR. It can add tests of its own. If it believes a test author's test is wrong, it may change it only with a reason in the PR, and the reviewer judges that change. It then opens the PR, with any `Confirm later` items in the header.

### 5.6 Reviewer

The reviewer works in a fresh context, as today, and sees only the PR and its issue. It checks the PR's claims against its own `make lint` and `make test` runs, and looks for ways the change falls short of the issue's goal. It also carries out the checks in §7.3, and reserves **Defect:** for what the cost of failure justifies. It can write only the "For human review" section of the PR body. A PR body reads in this order: the header, any `Confirm later` entries, Context and Task (copied from the issue by the worker, and checked by the reviewer as a note), Summary, then "For human review", then a `## Evidence` heading (it ends the section the merge gate reads) with the evidence collapsed below it.

## 6. Who decides what

Agents decide, unless the change falls into one of the groups below.

| Class | Examples | What happens |
|---|---|---|
| **Decide and record** | Interest rules, source curation, contract changes on current data, ADRs, dependencies, UI and design choices, evaluation expectations and scorer, infrastructure inside the agent scope | The agent chooses sensibly and adds a `Confirm later` entry. Development continues. |
| **Human merges** | Tier C in §9 | A PR waits for the human. Work that doesn't depend on it continues. |
| **Stop the line** | See below | The affected issue pauses and is flagged for the human. Everything else continues. |

**Stop the line** applies when work needs:

- credentials, secrets, or a GitHub setting or app install;
- a cloud change that an agent can't apply itself (see "Cloud resources");
- a new paid service, or a dependency with an incompatible licence;
- a migration or deletion of stored data that can't be recreated, or a live retention rule that deletes content;
- taking on a user who is not also a developer (see "Cloud resources");
- anything that could breach robots.txt, paywalls or other third-party limits;
- anything that could publish third-party article content in the public repo;
- a change of project goal, or a design choice that can't be reversed and that the plan doesn't settle.

In these cases the lead adds a comment on the issue with the shortest exact question or steps, and labels it `needs-human`. Other work continues. The human sees these in the lead chat or on GitHub.

**Example: where the line sits.** In PR #148 a story's canonical page was paywalled, but an openly served copy existed at another URL. The feed keeps the copy's text. That is decide and record, not stop the line, because the test is whether we bypassed anything: the copy was served openly, through the `Fetcher`, at its own URL. Reasoning like this, with the undo recorded, is what `Confirm later` is for.

### Cost of failure

An agent can only judge how much a problem matters if it knows what failure costs. Each project therefore keeps a short statement of its stage, the failures that are cheap and the failures that matter. Review depth, rework and warnings follow that statement, not how interesting a problem is.

- **Who owns it.** The human owns it, and the lead keeps it up to date. Agents never settle it alone.
- **Where it lives.** In `CLAUDE.md`, because every agent has that file in context, so no brief has to carry it. That file is Tier C, so agents can't lower their own scrutiny (principle 6).
- **When it is set.** At each milestone, which is the planned conversation. The report states the lead's understanding for the next phase and what changed, and merging the report confirms it. There's no separate meeting.
- **How it stays current.** The lead re-reads it at the start of each session, and names the line it relied on when it records a decision in the Phase Log, so the human can see its understanding in use and correct it. It proposes a change at once, as a Tier C PR, when the human corrects it or when the project gains a user who is not also a developer, data that can't be recreated, new spending, or anything newly public. A higher cost applies from the moment the lead sees it. A lower one waits for the merge.
- **How it reaches the work.** The phase design says where the phase differs from the statement, and issues that sit above it get `**Risk:** high`. The reviewer uses it to separate defects from notes (§7.3). The delivery manager and the lead use it at the review limit (§8). The lead uses it to size what it raises with the human: in development, a data problem that can be repaired or recreated is a note with its recovery step, not a warning.
- **What it can't do.** It never relaxes the stop-the-line list, the tiers, the architecture rules or an issue's acceptance examples.

### Recording a decision

The agent that made the decision adds this to the PR header. Decisions made outside a PR go straight to the Phase Log:

```
**Confirm later:** Weighted body-only demotions at 0.25. Reason: keeps relevant articles
visible while still ranking them lower. To undo: change `body_only_demotion` in config/interests.yaml.
```

Chosen dependencies also state the licence and why that package was picked. The delivery manager lists these in its batch report, and the lead copies them to the Phase Log (§4). The milestone report then lists them all. Anything the human rejects becomes a normal issue.

### Cloud resources

Agents need to create, change and destroy cloud resources during development, so they get a controlled scope (ADR-0007, which supersedes that part of ADR-0004):

- The human sets up the scope once: a separate development project, a service account limited to it, and short-lived credentials. No long-lived keys are stored on disk.
- The human does the first `terraform apply`, because it creates IAM members, budgets and secrets, which the service account can't. Those stay the human's after that.
- Inside that scope, agents can run `terraform apply`, `terraform destroy` and `gcloud` commands that change resources.
- `terraform apply` runs only after a change is merged, from up-to-date `main`, so nothing reaches the cloud before its review.
- Agents can't change IAM, billing or budgets, or delete the project. The service account's roles enforce this, so it doesn't rely on instructions.
- The development service account has no access to secret values. It has read-only roles, so that `terraform apply` can refresh the IAM, budget and secret resources it can't change. These include `roles/secretmanager.viewer`, which shows secret names and metadata, never values. Permission deny rules are prefixes that a reordered command can get past, so they are only a second line of defence behind these roles.
- Budget alerts don't stop spending, so the scope also needs quotas and resource limits.
- Resources are labelled so that leftovers are easy to find, and the milestone report lists anything still running.

**Production is out of scope for now.** Production starts when at least one user is not also a developer. Until then, every environment is a development environment. How to handle production will be decided when the project gets there, and agents must stop and ask before that point.

### Evaluations

Agents run and assess evaluations themselves. For each PR that touches `extract/`, the lead runs `make eval`, commits the generated `eval/results/` file to the branch and posts the before and after scores on the PR. Because an agent could improve a score by changing the expected answer, two rules apply:

- A change to `eval/urls.yaml`, the scorer or the scorer's data (`eval/boilerplate.txt`) goes in its own PR, never with extraction code.
- Every such change is a `Confirm later` item, and the milestone report shows the score before and after it.

## 7. Test-first development and review

### 7.1 Why both

Tests catch the cases somebody thought of, and review looks for the ones nobody did. A test that goes red then green shows it can fail, but it can still miss real behaviour. Writing tests from the issue without seeing the code removes the assumptions the code would otherwise pass on to its tests. The reviewer then tests whether the tests and the code hold up together.

### 7.2 The rules

- Every `feat` and `fix` follows the test-first flow in §8.
- A bug fix starts with a regression test that reproduces the bug.
- A feature that crosses stages has a test that runs through the real pipeline.
- Docs, config-only changes, spikes and tooling chores skip the test author. A `refactor` or `perf` change also skips it, because the existing tests must pass unchanged.
- Fixtures stay synthetic, and tests stay offline, as `CLAUDE.md` requires.
- Red tests never go on `main`.

### 7.3 What the reviewer checks

In addition to the current review, the reviewer:

- **confirms the red:** new tests must fail against the code from before the change. Tests for a new module fail on import, which proves little, so the next check matters more there;
- **runs a mutation spot-check:** breaks the key changed lines in a scratch copy and confirms that a test fails;
- **reads the test change report:** `tier.py` lists deleted or changed assertions, added skips and `live` markers, and any edits to the test author's tests. Each needs a good reason;
- **checks against `REVIEW.md`:** a list of failure patterns taken from real defects in this repo.

**What counts as a defect.** The reviewer does every check, and lists everything it finds, but marks an item **Defect:** only if it states the concrete failure and its cost, and at least one of these holds: it breaks an acceptance example or a hard rule in `CLAUDE.md`, it matches a failure that matters in the cost-of-failure statement (§6), or it would be seen routinely. Any estimated cost above the budget, even under a flood, is a defect, with its figures. A rare case with a cheap outcome is a plain note. Notes don't send a PR back. The delivery manager lists them in its batch report, and the lead adds them to the Phase Log as follow-ups.

**Needs human:** is for the stop-the-line list (§6), and also for a residual risk that matters that rework can only reduce.

### 7.4 `REVIEW.md`

`REVIEW.md` records what failure looks like here, and workers and test authors read it before starting. It grows when a review finds a new kind of defect. The delivery manager gets new lines merged before its batch report, so the next batch has them, instead of leaving them for the milestone. Examples:

- A field a feature reads must be set by the real pipeline, shown by a test that runs it.
- Code that promises never to raise must handle malformed input, including non-ASCII digits and malformed URLs.
- Every hop of a redirect must pass the robots.txt check.
- A destructive operation must be limited to files the code created.
- A test must fail when the behaviour it covers is broken.

## 8. The life of an issue (Gear 2)

1. **Ready.** `board.py promote` moves the issue to Ready once its dependencies are closed.
2. **Red.** The test author creates `issue-<n>-<slug>` and commits failing tests to it.
3. **Green.** The worker implements the issue, verifies it and opens the PR.
4. **CI.** Lint, test and Docker build must pass.
5. **Review.** A fresh reviewer writes its findings.
6. **Rework.** Defects return to a worker, and a new review follows if behaviour changed. There are at most two reviews. The limit is firm: only the lead can allow a third, with its reason in the Phase Log. A defect that matters (§6) is never merged on an unreviewed fix and never accepted as cheap: its fix gets a third review, on the rework only. Only cheap defects can be accepted without one.
7. **Evaluation.** If `extract/` changed, the delivery manager asks the lead to run the evaluation (§6). This comes before the next step because it adds a commit.
8. **Delivery review.** The delivery manager runs `make lint` and `make test` on the final commit, confirms CI is green, then posts a "Delivery review" comment with its verdict and the head SHA it checked. This replaces today's "Lead review". Any later push invalidates it.
9. **Merge.** The delivery manager merges Tier A and B through `tier.py merge`. A Tier C PR is opened as a draft and stays one until the Delivery review; the delivery manager then marks it ready and moves it to Human review on the board, so a ready PR tells the owner that agents are done. Before a PR joins the merge queue, and after each owner merge, the lead reads the issue and PR comments and turns any unactioned owner comment into a change or a follow-up.

If the second review still finds defects, the delivery manager doesn't loop. It passes the PR to the lead, which decides whether the issue is wrong, in which case the architect revises it, accepts what remains as cheap and marks it resolved with its reason, parks the PR, or refers it to the human. The fourth option is required when a defect that matters can't be fixed, only reduced: the human accepts the remaining risk, the lead recommends an answer and says which figures are estimates, and the merge stays refused until the human answers. Repeated defects on one path usually mean the issue names a symptom, not the rule, so the lead settles the design before any more rework. Parked PRs appear in the milestone report, and they stop the line only if other work depends on them.

A reviewer's **Needs human:** item also goes to the lead. The lead either treats it as stop-the-line, or decides it, records a `Confirm later` entry and marks the item resolved.

A chain of small dependent issues in one area can still go to one test author and one worker, as a single PR.

## 9. Risk tiers and the merge gate

`scripts/tier.py` keeps its role and classifies each PR from its diff.

| Tier | Contents | Who merges |
|---|---|---|
| **A** | Everything not listed in B or C, including all backend code, `client/`, tests and docs | Delivery manager |
| **B** | Decide-and-record changes: `models.py`, `ports.py`, ADRs, new dependencies, `config/interests.yaml`, `config/sources.yaml`, `backend/pyproject.toml` outside its `[tool.*]` tables, `eval/urls.yaml`, `eval/boilerplate.txt`, the eval scorer, test infrastructure (any `conftest.py` or `fakes.py` under `backend/tests/`, the network guard, the contract suites), `docs/feed.schema.json`, `docs/feed.example.json`, `REVIEW.md`, `PROJECT_PLAN.md`, `infra/` and Terraform files outside Tier C | Delivery manager, with a `Confirm later` entry |
| **C** | `.claude/`, `.github/`, `scripts/`, `backend/tests/scripts/`, `.gitignore`, `.dockerignore`, `backend/Dockerfile`, `AUTOMATION.md`, `CLAUDE.md`, `fetching/`, `docs/milestones/`; the files that define the checks (below); infrastructure changes that touch IAM or other access, billing, budgets, service accounts or delete rules, or that can destroy stored data (below); and any `needs-human` PR | Human |

**Files that define the checks.** Each tool takes the nearest configuration it finds, so these are Tier C by name in any directory: `Makefile`, `.pre-commit-config.yaml`, `pytest.ini`, `pytest.toml`, `tox.ini`, `setup.cfg`, `ruff.toml`, `mypy.ini`, `.coveragerc`, their dotted forms, any `pyproject.toml` other than `backend/pyproject.toml`, any `conftest.py` outside `backend/tests/`, and the client's equivalents (`tsconfig*.json`, ESLint, Prettier, Jest, Vitest and Playwright configuration). In `backend/pyproject.toml`, any change inside a `[tool.*]` table, or to a table header, is Tier C. `tier.py` reads the file at the head to tell, because a patch's context lines don't show which table a line is in. Renaming the file away from that path is Tier C, as is renaming a file onto it with no readable diff. A rename onto it with a readable patch follows the same rule as any other change. Dependency changes there stay Tier B, unless they need a `[[tool.mypy.overrides]]` entry.

**Infrastructure.** These rules cover `infra/` and any `*.tf`, `*.tfvars` or `*.tf.json` file, including a file moved out of `infra/`. A change is Tier C if:

- its whole patch, context lines included, mentions IAM, roles, `member` or `members`, policies, bindings, permissions, ACLs (including `google_storage_*_access_control`), public access, service accounts, billing or budgets, as a one-line `member` change names none of these on the changed line;
- its whole patch mentions a delete rule or retention: `lifecycle_rule`, a `Delete` action, a Firestore `ttl_config` or anything naming `retention`, as a shorter `default` under `variable "content_retention_days"` names it only on a context line;
- a changed line sets `force_destroy` to anything but `false`, changes a protection setting (`deletion_protection`, `delete_protection_state`, `deletion_policy`, `prevent_destroy` or `soft_delete_policy`), adds a `removed` block, or runs a delete command;
- it removes or relabels a `google_storage_bucket`, a `google_firestore_*` resource, `google_project` or a `module`, or changes the `count` or `for_each` of one;
- it renames a `*.tf` or `*.tf.json` file, because Terraform reads files by name and stops seeing what the old name defined, or renames any other of these files with no readable diff.

Bucket `name` and `location` changes stay Tier B: Terraform replaces the bucket, but refuses to destroy a non-empty one without `force_destroy`, which is Tier C. These checks read Terraform's own syntax, so in a `*.tf.json` file the `force_destroy`, `member`, `members` and store removal rules don't match: that file type gets the name-based rules only (IAM and role names, delete rules, protection and retention settings). The repo has no such file.

`tier.py merge` refuses unless all of these hold:

- the branch is in this repo, CI is green, and the PR is mergeable;
- it has no `hold` label, no **Needs human:** item and no unresolved **Defect:** item;
- it has an independent review, and the review states that the §7.3 checks were done;
- the "Delivery review" says "Verdict: merge" and names the head SHA;
- a Tier B PR has a `Confirm later` entry;
- a PR that touches `extract/` records its evaluation scores with no drop in the overall score, unless the lead gives a reason, which goes in the Phase Log.

A feature with resolved defects can now merge after a clean second review, or after the lead accepts what remains (§8), when the delivery manager, or a new one if the batch has ended, posts the Delivery review. One size limit applies to every PR: 400 source lines and 1,500 lines in total. The old feature conditions, including `Deviations: none` and `Tier A feature: confirmed`, are removed. The human can still revert any merge, or add `hold` to stop one.

The delivery manager sends an oversized PR back to a worker to split. On the 25 tiered PRs, these rules would have allowed 19 agent merges (76%) and left 6 for the human: three that set up the workflow itself, #125, which includes billing and IAM, #109, which added a `mypy` override, and #103, which edited a comment in one. #108 counts as an agent merge on the assumption that it would have been split. #110, #117 and #124 carried the old `needs-human` label, and count as agent merges because their files are Tier B and such changes are now decide-and-record.

## 10. Measuring it

The lead reports these at each milestone. The thresholds are starting proposals, and they get adjusted after the first milestone.

| Measure | Target |
|---|---|
| PRs merged by agents (pipeline code only) | At least 80% |
| Stop-the-line events per phase | As few as possible, each with a reason |
| Agent-merged PRs later reverted or needing a behaviour fix | No more than 1 in 10 |
| Decisions the human overturns at review | No more than 1 in 5 |
| Rework rounds per PR | Falling over time |
| Defects found by review that the tests should have caught | Falling over time |
| Issues reworked because the plan was wrong | Reported |
| Usage pauses per phase, and usage per issue | Reported |

If a failure rate is exceeded, the tier rules tighten through a normal Tier C PR. The test author's value is judged from these numbers too. If the extra agent adds cost without reducing defects, it can be limited to high-risk issues.

### The improvement loop

Friction is recorded as it happens and reviewed once per milestone:

- Each batch report has a "Friction" section: rework rounds and defects found, permission prompts (those the agents tell it about), usage pauses and cut-offs, failed gates and escalations.
- Workers, test authors and reviewers may add one line, `**Friction:** <what wasted effort>`, to their report. The delivery manager collects these into its batch report.
- The milestone report's "Process review" (§4) gives every friction item and proposal, and every follow-up with no issue, a status with its evidence and a recommendation: address, accept or defer. The human may add notes of their own.
- Once the milestone report is merged, the lead opens a `process` issue for each process change marked address that the human didn't reject, stating what should improve and which measure shows it, at most three per phase. `process` issues follow the normal tier rules, so changes to the automation are Tier C.
- The lead updates §15 at each milestone, including what didn't work.
- Friction data is kept in the Phase Log (§4): the lead merges each batch's "Friction" section into it, records its own friction there as it happens, and adds the human's notes.

| Signal | Suggests |
|---|---|
| Repeated rework on the same kind of defect | A missing test, check or line in a brief |
| Permission prompts for the same command | An allowlist entry, proposed as a human step |
| Usage pauses or cut-offs mid-batch | Batches too large, or a model choice too costly |
| Gates failing for non-substantive reasons | A rule that is too strict or badly worded |
| Escalations to the lead for the same reason | An issue template or architect gap |
| Parked PRs | Issues that are too big or unclear |

Permission prompts are counted only when an agent reports them. Usage per issue, overturned decisions and issues reworked because the plan was wrong have no source yet, and wait for automated metrics, which are deferred until one or two milestones show which numbers matter.

## 11. Operating limits

- **Parallelism:** up to 3 issues in progress, each using one test author and then one worker. Reviewers, the architect and the delivery manager don't count. No two in-progress issues touch the same files, and only one delivery manager runs at a time.
- **Sessions:** the lead finishes after 45 idle minutes, and restarting is cheaper than reloading a cold session. A usage pause doesn't count as idle.
- **Context checkpoint (not fully resolved, see §16):** the lead can't clear or compact its own context, and stopping would end unattended work, so it lets automatic compaction happen and makes sure little depends on the summary. At each batch close it runs `python3 scripts/lead.py context`. At 150k tokens or more, or after three batches if that can't be read, it curates the Phase Log, rewrites the handoff note, tells the human they can restart to save usage, and starts the next batch. A batch costs about 55k tokens, so compaction may come mid-batch. Agents keep running through it, and a `SessionStart` hook sends the lead back to its handoff note, the Phase Log and its open work. The lead also records each batch in the handoff note as it starts. Agents don't depend on the lead's memory: a delivery manager with an unanswered request sends it again.
- **GitHub API:** 5,000 GraphQL points an hour are shared across every agent, so prefer REST in loops.
- **Worktrees:** each agent works in its own, under `.claude/worktrees/`.
- **Permissions:** agents work within the allow list in `.claude/settings.json`. A permission prompt would wait for the human, so the list must cover every routine step, and no agent uses `--dangerously-skip-permissions`.
- **Credentials:** agents use only the short-lived, limited identity described in §6. They never handle keys or secrets directly.

### The compaction hook

Appendix A has the code, for use on other projects. `.claude/settings.json` has a `SessionStart` hook with the `compact` matcher, which Claude Code runs when a session restarts from compaction. It prints one line, which is added to the new context:

> Context was just compacted. If this session ran /lead-start, follow its After compaction section before anything else. Subagents and any other session: ignore this.

That section of `/lead-start` has the lead read its handoff note and the Phase Log, then check running agents, scheduled checks and open PRs waiting on it. The hook doesn't compact, clear, start or stop anything. It only tells the lead what to do next.

- **Why `SessionStart`:** `PostCompact` can't add to the context. Its documented use is side effects, such as logging the summary.
- **Why plain text:** every session sees the line, and the test is a fact a session can check about itself, so the lead always gets it. Limiting the hook to the lead would need a marker file keyed by session ID, because a variable set by a Bash command never reaches the hook. A broken marker would silently hide the message from the one session that needs it, while a wrongly read message costs a few harmless reads.
- **Who changes it:** the human, because agents can't edit `.claude/settings.json`.

### Usage limits

Two limits apply: a 5-hour window and a weekly one. Agents are cut off when either reaches 100%. That costs no money and does no damage, because agents only repeat some work, so the aim for the 5-hour window is to use it fully and recover cheaply. The weekly limit needs more care, because reaching it can stop development and the human's own use for days.

`scripts/usage.py` reads both limits and their reset times from the usage endpoint, which has proved reliable.

- **Delivery manager:** checks `usage.py` before starting each issue. When either limit is at about 90%, it avoids starting a new issue, and tells the lead which limit it is and when it resets. Agents already running carry on, then it reports and ends. If it prints `usage unavailable`, it carries on and tells the lead, so that pacing can't turn off unnoticed.
- **Lead:** for a 5-hour pause, schedules a one-shot check for shortly after the reset, then starts a new delivery manager, which picks up from the board. For a weekly pause, it starts no new batches and flags the pause and reset time for the human, leaving the remaining allowance for the human's use.
- **Workers and test authors:** commit and push regularly, so a cut-off loses little.
- **Human:** shares both limits, and can tell the lead to pause or to carry on past 90%.

If an agent is cut off, its branch and worktree remain, and a new worker carries on from there. If usage can't be read, work continues as normal. A scheduled check only fires while the lead's session is open. Otherwise, the next `/lead-start` picks the work up, and no timer outside the session restarts it.

## 12. Moving to this model

This ships as one Tier C PR, which the human approves by merging:

- **New:** `AUTOMATION.md`, `REVIEW.md`, `.claude/agents/architect.md`, `delivery-manager.md`, `test-author.md` and `scripts/usage.py`.
- **`scripts/usage.py`:** reuses the usage hook's lookup, and adds the weekly limit.
- **`scripts/tier.py`:** the new tiers, merge gate (including the "Delivery review" name), test change report and an `agent-merged` label in place of `lead-merged`, with tests.
- **`.claude/agents/worker.md` and `reviewer.md`:** the steps in §5 and §7.
- **`.claude/skills/lead-start` and `lead-finish`:** the phase flow, batches, decisions list and milestone report. Waiting for a usage reset no longer counts as idle, so the work isn't stranded.
- **`CLAUDE.md`:** shortened, with its workflow section pointing here. Architecture rule 2, the ADR rule and "Things agents must not do" change to match §6.
- **`.claude/settings.json`:** the deny rules for `terraform`, `gcloud` and `eval/urls.yaml` change to match the agent scope, and the allow list covers the new roles.
- **`PROJECT_PLAN.md`:** §9.4 and §9.5 point here, §6 gains the milestone demo for each remaining phase, and §8 lists the new root files and `docs/` folders.
- **Templates:** the PR header gains `Confirm later`, and issues gain acceptance examples.

Two steps sit outside the PR. The human sets up the development project, its service account and credentials (§6) once, before agents use them. The local usage hook is removed from `.claude/settings.local.json` when the PR merges, because that file is git-ignored.

**Pilot.** The first batch after merging is a small pilot. It confirms that the delivery manager can run agents in parallel in the background, handle their results as they finish, and reach the lead mid-batch. If that fails, the fallback is two top-level sessions, with Gear 1 and Gear 2 side by side using `SendMessage`. The first real cut-off at the usage limit will show whether the lead's scheduled check still runs afterwards.

The six PRs already open (#123, #124, #125, #126, #129, #130) finish under the current rules. The remaining Phase 2 issues use the new model as soon as it merges, so Phase 2 gets the first milestone review.

## 13. Where things live

Each kind of information has one home, and other documents link to it instead of repeating it.

```
/
├── README.md               what the project is and how to run it
├── PROJECT_PLAN.md         aims, requirements, architecture, phases
├── AUTOMATION.md           how the project is built: this document
├── CLAUDE.md               day-to-day rules and commands for agents
├── REVIEW.md               failure patterns from real defects
├── docs/
│   ├── adr/                architecture decisions
│   ├── phases/             phase designs: phase-<n>.md (new)
│   ├── milestones/         milestone reports: phase-<n>.md (new)
│   └── ...                 reference docs, such as the feed schema
├── .claude/
│   ├── agents/             agent briefs, one file per role
│   └── skills/             /lead-start, /lead-milestone and /lead-finish
├── .github/                CI, issue templates, PR template
├── scripts/                board.py, context.py, lead.py, tier.py, usage.py
├── config/                 sources, interests, extraction rules
├── eval/                   evaluation expectations and results
├── backend/                pipeline code and tests
├── client/                 reader client (Phase 3)
└── infra/                  Terraform
```

| To find | Look in |
|---|---|
| What the product should do | `PROJECT_PLAN.md` |
| How the code is laid out | `PROJECT_PLAN.md` §8 |
| How a phase will be built | `docs/phases/phase-<n>.md` |
| Why a lasting design choice was made | `docs/adr/` |
| What a phase delivered, and what the human confirmed | `docs/milestones/phase-<n>.md` |
| Rules for how agents work | `AUTOMATION.md`, then `.claude/agents/` for each role |
| Current work and its status | GitHub issues and the Project board |
| Decisions, friction and follow-ups for the current phase | The "Phase N log" issue, until the milestone report records them |
| Work put off on purpose, across phases | `DEFERRED_WORK.md`, reviewed at each milestone |
| How the lead prepares a milestone and acts on sign-off | `.claude/skills/lead-milestone/SKILL.md` |
| What failure costs right now | `CLAUDE.md`, "Cost of failure" |

Some things are deliberately kept out of the repo:

- **Evaluation snapshots:** third-party content, kept in `NEWSFEED_FIXTURES_DIR`.
- **Evaluation logs and worktrees:** `eval/logs/` and `.claude/worktrees/` are git-ignored.
- **Local settings and secrets:** `.env` and `.claude/settings.local.json` are git-ignored.
- **The lead's handoff note:** kept in Claude Code's memory folder, because it describes one session's state.

`docs/phase1-review.md` stays where it is, because ADR-0003 links to it. It's the only milestone report outside `docs/milestones/`.

## 14. Decisions

| Date | Decision | Reason |
|---|---|---|
| 2026-10-11 | Lead, up to 3 workers and a read-only reviewer | Parallel work with independent review |
| 2026-10-17 | Risk tiers from the diff (#67, #70) | Spend human attention by risk |
| 2026-10-31 | Reject the Two-Gear Factory daemon | It adds supply to a queue that was already stuck, its test-only gate misses this project's real defects, and it conflicts with the `main` ruleset |
| 2026-10-31 | Keep the two-gear split of roles | Planning and delivery compete for attention, and reversible decisions need an owner |
| 2026-10-31 | The lead is Gear 1 and owns the phase | One accountable agent for goals and fit with later phases |
| 2026-10-31 | Add an architect subagent | Keeps issue-level planning out of the lead's context |
| 2026-10-31 | Milestone reviews with a working demo | One planned touch point, based on software the human can use |
| 2026-10-31 | Reversible decisions are recorded and confirmed later | Waiting costs more than an occasional correction |
| 2026-10-31 | Test-first by a separate test author for all `feat` and `fix` work | Independent tests from the issue, and good practice at low extra cost |
| 2026-10-31 | Code review, including test results, stays the acceptance gate | Passing tests missed more than 40 defects that review caught |
| 2026-10-31 | No red tests on `main` | They would fail required CI for every other PR |
| 2026-10-31 | Building stops at a milestone until the human's positive sign-off, with no time-based decision, and the architect may draft the next phase | Nothing is built on unconfirmed decisions |
| 2026-10-31 | Agents manage cloud resources inside a limited scope (ADR-0007) | Development needs cloud changes |
| 2026-10-31 | Production is out of scope until decided later, and starts when a user is not also a developer | Nobody but the developer is affected until then |
| 2026-10-31 | The human follows the project through the lead chat and GitHub | Either is enough, so push notifications aren't needed yet |
| 2026-10-31 | Agents run and assess evaluations, with expectation changes reviewed at milestones | Keeps development moving, and the milestone review guards against agents marking their own work |
| 2026-10-31 | Oversized PRs are split | Keeps review effective without a human step |
| 2026-10-31 | Phase designs and milestone reports are files in `docs/`, reviewed by PR | Lasting records belong in the repo with the code |
| 2026-10-31 | The test author's cost is reviewed at the first milestone | One extra agent per issue is unmeasured against the 5-hour limit |
| 2026-10-31 | A delivery manager subagent runs Gear 2, fresh for each batch | A phase produces too many agent reports for the lead's context, and fresh batches keep Gear 2 small. Parallel sessions were the alternative |
| 2026-10-31 | Retire the usage hook in favour of light pacing and automatic resume | Reaching the limit costs nothing but some repeated work, so a hard stop is out of proportion |
| 2026-10-31 | Respect the weekly limit too, and pause at about 90% until the human decides | Reaching it can stop development and the human's own use for days |
| 2026-10-31 | No restart from outside the lead's session for now | It needs a safe headless setup, and pauses aren't yet a proven problem |
| 2026-10-31 | The files that define the checks, wherever they sit, including `[tool.*]` tables in `pyproject.toml`, and the tests of the merge gate, are Tier C | Otherwise an agent could weaken `make lint`, `make test` or `tier.py`'s tests without the human, against principle 6 |
| 2026-10-31 | `terraform apply` runs only after merge, and the development service account has no access to secret values | Review comes before anything reaches the cloud, and the limit on secrets rests on roles, not on deny rules |
| 2026-10-31 | The development service account gets read-only roles, including `roles/secretmanager.viewer` (names and metadata, never values) | `terraform apply` must refresh the resources it can't change, or "Agents apply later changes" can't work |
| 2026-10-31 | Monitor the way of working with a light loop: friction notes, a batch "Friction" section and a milestone "Process review", with at most three `process` issues per phase | Without a source for the numbers and an owner for the changes, friction would stay in place; automated metrics wait until it is clear which ones matter |
| 2026-10-31 | Record learnings in §15 | The human wants to reuse this document on other projects, and the evidence is freshest now |
| 2026-11-01 | The lead keeps a curated Phase Log as a GitHub issue, and writes the milestone report from it | Collating at the end loses things, and a repo file would need a PR for every update |
| 2026-11-01 | State the cost of failure in `CLAUDE.md`, confirmed at each milestone, and reserve **Defect:** for failures that justify rework | Without it, rare and cheap cases took four and five review rounds, and warnings outgrew the project's stage |
| 2026-11-01 | The two-review limit is firm, and only the lead can allow a third review | It was passed twice in the pilot with no decision recorded |
| 2026-11-01 | Tier C PRs are drafts until the Delivery review; the lead reads owner comments before queueing and after each merge; a defect that matters always gets a reviewed fix, and can be referred to the human | PRs #160 and #167 merged with work or comments outstanding, and the two-review limit was ambiguous for defects that can only be reduced |
| 2026-11-01 | At 150k tokens the lead checkpoints at a batch close and carries on, and a hook recovers it after compaction | Stopping would end unattended work, and neither the lead nor a timer can run `/clear` or `/compact`, so durable state protects it instead |
| 2026-11-01 | This document describes the method, and no agent needs it in context | The briefs and skills are the implementation, and reading it costs the lead 12k tokens a session |
| 2026-11-01 | Process steps are defaults the human can waive, and work can carry forward at a milestone | The steps exist so nothing is lost and work stays efficient. An agent can use judgement, so rigid steps would only become barriers |

## 15. Learnings

What building this way has taught so far, written to be reused on other projects. The delivery manager and milestones have now run once. The architect hasn't, and the test author's value is still unproven (`docs/milestones/phase-2.md`).

### Conceptual

- **Measure the bottleneck before designing a fix.** PR tier data showed that path rules, not a lack of execution, were holding work (§2). The proposed daemon would have added supply to a stuck queue.
- **Reversibility decides human involvement.** Cheap-to-change decisions shouldn't wait. Harm to third parties, money, secrets and lost data should.
- **Spend human time on working software and decisions.** A milestone with a demo gives the human more than reading diffs does.
- **Tests and review do different jobs.** Tests check the cases somebody thought of, and independent review looks for the rest. Reviewers found more than 40 defects in work that had passed its tests.
- **Separate authors help.** Tests written from the issue don't inherit the code's assumptions.
- **Keep orchestration out of the accountable agent's context.** Use one fresh delivery manager per batch, so the lead's context stays on the phase.
- **A hard stop can be out of proportion.** Reaching the 5-hour limit costs only repeated work, so pacing and resume beat a blocking hook. The weekly limit needs more care.
- **The rules that govern agents need the closest scrutiny.** They fail quietly, and agents shouldn't be able to loosen them.
- **Review has diminishing returns on rule code.** #133 had four reviews, against §8's limit of two, and the later ones found narrower gaps. Agree a stopping rule, and treat tier rules as a safety net against mistakes, not a security boundary.
- **A single large process PR is expensive to review.** #133 mixed rule changes with document changes. Split them where possible.
- **Tell agents what failure costs.** Reviewers given no sense of stakes treat every gap as a defect. State the stakes once, where every agent sees them, and have the human confirm them at each milestone.
- **Settle the rule before reworking.** #131 and #146 took four and five rounds because their issues named a symptom. #147, with its design settled first, took two.
- **Measure agent merges on product code.** In the first phase under this model, 15 of 18 PRs changed the automation or infrastructure, which is Tier C by rule, so the share of agent merges said nothing about trust.
- **A human's merge can overtake agents.** PR #160 merged during its review, and PR #167 merged with a comment unactioned. A draft shows the status of a review, but it doesn't stop a comment being missed, so the lead has to check for comments too.
- **What didn't work:** a rule with no enforcement. The two-review limit was passed twice, because nothing enforced it. A delivery manager brief saying "one report" was also missed in every batch, at no real cost.
- **Don't shape a design around process friction.** Phase designs briefly moved from a file to an issue, only because nobody owned the file's PR. The fix was to assign an owner.

### Practical

- **Test a tool's capability before designing around it.** The assumption that subagents can't start subagents was wrong, and one small test settled it.
- **Write check rules against real data shapes.** A retention fix in #133 passed a test whose input wasn't in the real file, and in #123 a report section was always empty on real data, because only the unit tests set its field.
- **Match the whole class of change, not one example.** Read the whole patch when context decides meaning: a `member` or `default` line in #133 shows its risk only through the lines around it.
- **Keep line-based gate regexes on their line.** `\W` and `\s` cross newlines, so an empty `Reason:` passed in #133. Placeholders like `n/a` must not satisfy a gate either.
- **Check briefs under the isolation the agent runs with.** Isolated agents are refused git against sibling worktrees and in compound commands (#133).
- **Stubs must copy real side effects.** A stub `terraform init` that wrote no lock file hid a defect in #136 that would have stopped the first real run.
- **A mutation spot-check is cheap.** It finds tests that can't fail, as it did for `DELETE_RULE_RE` in #133.
- **A "Resolved" marker is a claim to verify, not evidence.** #133's fourth review found one that overclaimed.
- **Agents can't edit their own permission settings.** Plan that change as a human step, as #134 does, and never route round a refusal.
- **Agents post as the owner's account.** GitHub doesn't notify the owner of those comments, so status must be visible in the lead chat or on the board.
- **Check token scopes after a `gh` re-login.** The board needs the project scope, and an invalid token falls back silently to 60 anonymous calls an hour.
- **Reversing a recorded decision needs a new ADR.** A reviewer caught ADR-0004 being overturned without one (#133, now ADR-0007).
- **`Closes #N` closes an issue on merge even if human steps remain.** #125 closed #74 before the human's first apply.
- **Rebases rewrite SHAs.** Remap "Resolved in" markers afterwards, and use each merge order to limit conflicts on shared files.
- **The role that can run a check must own it.** `make eval` can't run in subagents, so the lead owns it.
- **A promise to record something is not a record.** A slip reported to the human as going in the Friction notes was only in the handoff note, which the next session overwrites. Write it down in the same step, and reconcile before finishing.
- **Measure agent context from the transcript.** Claude Code records token counts per turn. Three batches took the lead from 44k to 210k tokens, about 55k a batch, and most of that was its own work, not file reads.
- **The git stash list is shared across worktrees.** Never use bare stash commands in a shared repo.

## 16. Open questions

### How the lead keeps working as its context fills

**Status: not fully resolved.** §11 describes the current approach, which is a working choice and not a settled answer. Review it at the first mid-batch compaction, and at the first milestone at the latest.

The problem: the lead's context grew from 44k to 210k tokens over three batches, about 55k a batch, and most of that was its own work. Automatic compaction summarises the context at an unplanned moment, and a large context uses the usage limits faster because every turn re-sends it. The lead can't clear or compact its own context, and a timer can't either: scheduled prompts run skills, but built-in commands reach the model as plain text. So a timer can resume work in the same session, but it can't empty the context.

| Option | For | Against |
|---|---|---|
| **Checkpoint and carry on, with a recovery hook (current)** | Unattended work continues, no new infrastructure, and little depends on the summary | Compaction can land mid-batch, and the context stays large between compactions, which costs more usage |
| **Stop at a threshold and wait for the human** | A clean handoff at a chosen moment, and a small context | Ends unattended work after about three batches, against the aim in §1. Rejected, though the human can still choose to restart when present |
| **Restart from outside the session** | Clean restarts with no human, and the only unattended way to keep context small | Reverses §14 and needs a spike: `claude -p` waits about 10 minutes for background agents by default, a permission prompt has nobody to answer it, the human can't follow the session in the chat, and the machine must stay on. Cloud scheduled tasks have no local evaluation snapshots |
| **Leave compaction to happen unaided** | The simplest, and it hasn't caused harm yet | Nothing makes sure the record is fresh, or brings the lead back to its open work |
| **Use less lead context per batch** | Delays the problem, and also saves usage | Moves decisions away from the agent keeping the overview, and doesn't remove the limit |

Still unknown:

- whether recovery works when compaction lands mid-batch, which only a real case can show;
- whether scheduled checks survive compaction, on which the docs are silent;
- how much usage a large context really costs;
- whether 150k is the right threshold, as it rests on one session.

The Phase 2 milestone reviewed this: the hook fired on a manual `/compact`, and the approach stays. Evidence goes in the Phase Log. If compaction proves costly, the next step is a spike on restarting from outside the session.

## Appendix A: the compaction hook

§11 explains why this exists. To use it on another project, copy the two parts below, then replace `/lead-start` with the skill that starts your orchestrating session. It suits any long-running session that keeps its state outside the conversation.

**1. The hook.** Merge this into `.claude/settings.json`. The hook's output is added to the context after compaction.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Context was just compacted. If this session ran /lead-start, follow its After compaction section before anything else. Subagents and any other session: ignore this.'"
          }
        ]
      }
    ]
  }
}
```

**2. The recovery steps.** Add this section to the start skill, and change the steps to match where your project keeps its state.

````markdown
## After compaction

A hook tells you when your context has just been compacted. Agents keep running through it, so carry on without restarting anything:

1. Read the handoff note and the Phase Log.
2. Run `ListAgents`, and don't start a second delivery manager if one is running.
3. Run `CronList`. If the handoff records a usage pause and its check is gone, schedule it again.
4. Check the open PRs for anything waiting on you, such as an "Evaluation scores" comment that was requested and is missing.

If the handoff is out of date, trust the board and GitHub.
````

**To test it,** start a new session, because hooks load at session start, run the start skill, then run `/compact`. The session should follow the steps. Other sessions should ignore the message.

**Limits.** The hook needs `SessionStart` with the `compact` matcher, because `PostCompact` can't add to the context. It fires only when compaction happens, and neither the model nor a timer can trigger that. It tells the session where to look, so it helps only if the state there is current.
