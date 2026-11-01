# ADR-0008: The development scope is the existing project

- **Status:** Accepted
- **Date:** 2026-11-01
- **Related:** #192; partly supersedes [ADR-0007](0007-agent-infrastructure-scope.md) (the "separate development project" part of "The scope"); `infra/README.md` ("The development identity"); `AUTOMATION.md` §6.

## Context

ADR-0007 let agents apply infrastructure inside a limited development scope and described that scope as a separate project. What limited the damage there was the separation: a mistake could only reach throwaway resources. The owner has since set the scope up in the existing project instead. Production hasn't started (every environment is a development environment, and the owner is the only user), so the existing project holds the only real data, including the owner's feedback.

## Decision

**The existing project is the development scope. No second project is created.** The rest of ADR-0007 stands.

- **What limited damage before.** The separate project: nothing in it was worth losing, and the account had no route to anything else.
- **What limits it now.** The development service account's roles (listed in `infra/README.md`), reached by impersonation with short-lived tokens and no key files; no IAM, billing, budget or secret changes; no direct write access to Firestore or the content bucket. These roles are not the whole limit: with `run.developer`, `iam.serviceAccountUser` on the runtime account and `artifactregistry.repoAdmin`, the account can run code as the runtime account, which reaches Firestore, the content bucket and secrets. `infra/README.md` ("The risk") says so, and the owner accepts it by merging or removes the grants.
- **Variables and state.** ADR-0004's single root module and state stay as they are, so ADR-0007's remark that a development scope may need its own variables and state doesn't apply.

## Consequences

- The limit on damage is weaker than a separate project gave, and the owner's data is within reach of a mistake. The Cost of failure section of `CLAUDE.md` ranks losing feedback as mattering, so the residual risk is on record rather than assumed away.
- The setup is documented and repeatable.

**Revisit when**

- production starts (the scope and the data must then be separated);
- the owner wants the Cloud Run and Artifact Registry grants removed, or a second project to restore the old limit.
