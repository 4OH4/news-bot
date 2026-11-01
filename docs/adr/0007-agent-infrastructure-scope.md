# ADR-0007: Agents apply infrastructure inside a limited development scope

- **Status:** Accepted, except that the scope is a separate project, and that the development account "has no access to secret values" (no longer true of the account, and its spending is not bounded by quotas): partly superseded by [ADR-0008](0008-development-scope-is-the-existing-project.md)
- **Date:** 2026-10-31
- **Related:** #132, `AUTOMATION.md` §6 ("Cloud resources"); supersedes the "who applies changes" parts of [ADR-0004](0004-infrastructure-as-code.md); `PROJECT_PLAN.md` NFR-2, NFR-6.

## Context

ADR-0004 chose Terraform with a small `gcloud` bootstrap, and rested on "only the human applies changes": agents write infrastructure code but never run `plan` or `apply`, and never hold GCP credentials. Its "Revisit when" list names someone other than the human applying changes as a reason to reopen that.

The phase-based automation model (`AUTOMATION.md`, #132) makes that reason real. Agents now build a whole phase with little human input, and development needs cloud resources to be created, changed and destroyed along the way. If each of those waited for the human, cloud work would stay the bottleneck that the model removes. The risks that led to ADR-0004 remain: a bill, a leaked secret or deleted data can't be reverted.

## Decision

**We will let agents run `terraform apply`, `terraform destroy` and `gcloud` commands that change resources, but only inside a limited development scope.** This replaces the following in ADR-0004: the force "Only the human applies changes", "Only the human runs either" under Decision, section 5 ("Who applies changes") as far as it concerns agents, and the "agents write, the human applies" consequence.

- **The scope.** The human sets it up once: a separate development project, a service account limited to it, and short-lived credentials. (Partly superseded by ADR-0008, which uses the existing project.) No long-lived keys are stored on disk. Budget alerts don't stop spending, so the scope also has quotas and resource limits. Resources are labelled, so leftovers are easy to find, and the milestone report lists anything still running.
- **The first apply.** The human does the first `terraform apply` in that project, because `infra/terraform/` includes IAM members, a budget and secrets, which the service account can't create. Agents apply later changes.
- **The limits.** Agents can't create or change IAM, billing, budget or secret resources, or delete the project: a change to those is the human's to apply. The development service account has no access to secret values. It has read-only roles, so that `terraform apply` can refresh the resources it can't change, including `roles/secretmanager.viewer`, which shows secret names and metadata, never values. The service account's roles enforce these limits, so they don't rely on instructions, and permission deny rules are only a second line of defence. Anything else outside the scope stops the line (`AUTOMATION.md` §6) and goes to the human.
- **Production is out of scope for now.** Production starts when at least one user is not also a developer. Until then every environment is a development environment. How to handle production will be decided when the project gets there, and agents must stop and ask before that point.
- **Review.** Infrastructure code still arrives by PR, and `terraform apply` runs only after that PR is merged. `scripts/tier.py` makes changes that touch IAM or other access, billing, budgets, service accounts or delete rules, or that can destroy stored data, Tier C, which the human merges, and other `infra/` changes Tier B, with a `Confirm later` entry.

The rest of ADR-0004 stands: Terraform and the bootstrap split, state in the bootstrap bucket, provider pinning, and secret values staying out of the repo and out of state. Secret values remain the human's to add, because agents never handle secrets. Outside the development scope, only the human applies changes, as ADR-0004 says. CI still never runs `plan` or `apply`.

### Alternatives considered

- **Keep human-only apply.** Safest, but every cloud change would wait for the human, which is the delay this model is meant to end.
- **Let agents apply everywhere.** Rejected: nothing would limit the damage from a mistake, and the human would have nothing to fall back on.
- **Apply from CI, through Workload Identity Federation.** Possible later, but it needs its own decision on credentials, and gives no benefit while agents already work locally.

## Consequences

**Positive**

- Agents can build and test cloud work end to end, and the human reviews the result at the milestone.
- The limits sit in the service account's roles, so a mistaken instruction can't widen them.

**Negative**

- The human has to set up the scope before agents can use it. Until then agents have no credentials, and `.claude/settings.json` still denies `terraform` and `gcloud`.
- The human no longer sees a `terraform plan` before each development change. The damage is limited by the roles, quotas and labels above, not by review.
- ADR-0004's single root module assumes one environment, so a development scope may need its own variables and state (not needed now: see ADR-0008).

**Revisit when**

- a user who is not also a developer is about to start, which is the production decision;
- the development scope proves too narrow or too costly;
- applying from CI is wanted.
