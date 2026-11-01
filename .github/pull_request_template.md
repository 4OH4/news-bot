Closes #<!-- N; one line per issue for a chain --> · **Tier** <!-- A/B/C, from `python3 scripts/tier.py classify <PR>` --> · **Merge after:** <!-- "any order", or the PRs that must merge first -->
**Confirm later:** <!-- Each decision the issue didn't settle: what you chose, the reason, and how to undo it. A dependency also states its licence and why that package. Required on Tier B. Delete this line if there are none. -->

## Context
<!-- The issue's opening plain-language Context paragraph, word for word, without its References. For a chain: the first issue's paragraph, plus one line for each other issue. If the issue has none, one short plain paragraph from the issue. -->

## Task
<!-- The issue's **Summary:** paragraph from its Task, word for word, without the Details. Same rules for a chain or a missing paragraph. -->

## Summary
<!-- Two or three plain sentences: what was actually done and why. -->

## For human review
<!-- Pending independent review. Filled in by the independent reviewer: judgement calls you approve by merging, risks and open questions, one or two lines each, most important first. On a Tier C PR, open with a bold **Why Tier C:** line naming what makes it Tier C and what could go wrong with each; end with a **Lower priority:** group of minor notes. Leave out empty parts; "None" if there is nothing at all. This and the sections above are all the human has to read; everything below is evidence, collapsed. -->

## Evidence

<details><summary>Acceptance criteria (<!-- n/n --> met)</summary>

<!-- The issue's checklist, ticked, with a short pointer to the evidence. For a chain, one list per issue. -->
- [ ]

</details>

<details><summary>Verification</summary>

```bash
make lint
make test
```

<!-- Commands run and their results. If extract/ was touched, the lead adds the evaluation scores in a comment. -->

</details>

<details><summary>Contracts, design notes and follow-ups</summary>

- [ ] No changes to `models.py` or `ports.py` (or: this is the contract change's own PR)
- [ ] No cloud SDK imports outside `adapters/`

**Design notes:** <!-- Notable choices worth capturing as an ADR, or "None". -->

**Follow-ups:** <!-- New issues this work revealed, one line each, or "None". -->

</details>
