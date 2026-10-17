Closes #<!-- N; one line per issue for a chain --> · **Tier** <!-- A/B/C, from `python3 scripts/tier.py classify <PR>` --> · **Merge after:** <!-- "any order", or the PRs that must merge first --> · **Deviations:** <!-- "none", or each departure from the issue: scope change, judgement call the issue didn't settle, unforeseen problem -->

## For human review
<!-- Filled in by the independent reviewer: judgement calls you approve by merging, risks and open questions, one or two lines each. "None" if there are none. -->

## Summary
<!-- Two or three plain sentences: what changed and why. This and the section above are all the human has to read; everything below is evidence, collapsed. -->

<details><summary>Acceptance criteria (<!-- n/n --> met)</summary>

<!-- The issue's checklist, ticked, with a short pointer to the evidence. For a chain, one list per issue. -->
- [ ]

</details>

<details><summary>Verification</summary>

```bash
make lint
make test
```

<!-- Commands run and their results. If extract/ was touched, the before/after `make eval` summary. -->

</details>

<details><summary>Contracts, design notes and follow-ups</summary>

- [ ] No changes to `models.py` or `ports.py` (or: linked `needs-human` issue #___)
- [ ] No cloud SDK imports outside `adapters/`

**Design notes:** <!-- Notable choices worth capturing as an ADR, or "None". -->

**Follow-ups:** <!-- New issues this work revealed, one line each, or "None". -->

</details>
