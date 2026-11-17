# Module publish-readiness — 19-clone sweep (2026-11-05)

Pass across all 19 Terraform module clones under `/Users/Shared/github.com/catonetworks/`
(the 18 `terraform-cato-bulk-*` modules plus `terraform-cato-socket-bulk-sites`) to bring
them to a consistent, Terraform-Registry-publishable state before the repo-creation /
tagging / registry-add step (see `docs/release-instructions.md` §0 and §2 for that
sequencing).

**Convention — "canonical README"**: `terraform-cato-bulk-if-rules/README.md` is the
repo owner's reference format for all 19 modules:

```
Title
## Usage
  ### Basic Example
  ### JSON Configuration Structure (+ #### Key Fields:)
  ### How Rule Ordering Works
  ### Parameters
## Working with Existing Rules (Brownfield Deployments)
  ### Installing and Configuring Cato CLI
  ### Exporting Existing Rules
  ### Importing Existing Rules into Terraform State
  ### Typical Brownfield Workflow
  ### Backup and Restore Workflow
## Sub-policies                      (only where the family has them)
## Using Module Outputs (+ ### Output Use Cases)
## Requirements / ## Providers / ## Modules / ## Resources / ## Inputs / ## Outputs   (terraform-docs tail)
```

Every other module's README was restructured into this exact heading order while
preserving its own family-specific content (export command, resource names, JSON root
path, `--module-name`) — never genericized. `README canonical` below is **yes** where
the file already matched (no edits needed) and **fixed** where headings were
reordered/added.

No `git commit` was run anywhere. No provider version constraints were changed in any
`versions.tf` (the `>= 1.3.1`-style floors, and the socket-bulk-sites
`= 1.5.0-prerelease.2` pin bumped separately beforehand, were left exactly as found).

**Upstream existence**: `git ls-remote origin` failed for every clone in the sandbox
used for this pass with `Permission denied (publickey)` (no SSH key loaded) — a sandbox
auth gap, not a repo-existence signal. Ground truth was instead confirmed with an
unauthenticated GitHub API check, `curl https://api.github.com/repos/catonetworks/<repo>`,
which returned **HTTP 200 for all 19 repos**, including `terraform-cato-bulk-ao-rules`
and `terraform-cato-bulk-atr-rules` — both repos already exist upstream today, which
supersedes the stale "no origin remote, create the repo" note in
`docs/release-instructions.md` §0 (their local clones' `.git/config` already carry the
correct `origin` URL too).

## Checklist

| Module | README canonical | LICENSE | fmt | validate | guard present | upstream repo exists | planned first tag | Notes |
|---|---|---|---|---|---|---|---|---|
| terraform-cato-bulk-if-rules | yes | ok | pass | pass | yes | yes | v0.0.9 | Canonical reference itself; confirmed structurally correct as-is, no edits needed. |
| terraform-cato-bulk-wf-rules | fixed | ok | fixed | pass | yes | yes | v0.0.10 | Reordered misplaced full-JSON `<details>` example before "How Rule Ordering Works"; fixed a broken stray heading in the Backup/Restore section; fmt fix was whitespace-only. |
| terraform-cato-bulk-wnw-rules | fixed | ok | fixed | pass | yes | yes | v0.0.2 | Same two README fixes as wf-rules (JSON example reorder + broken heading); no Sub-policies section (module has none, correctly omitted); fmt fix was alignment-only. |
| terraform-cato-bulk-tls-rules | yes | ok | fixed | pass | yes | yes | v0.0.2 | Structure already canonical (its extra "Best Practices for TLS Rules" section is additive and kept); fmt fix was alignment-only; no Sub-policies section (none exist). |
| terraform-cato-bulk-lan-fw-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured into canonical order with new Sub-policies and Using Module Outputs sections; no code/version changes. |
| terraform-cato-bulk-app-control-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured into canonical order (Sub-policies correctly omitted — flat policy); pre-existing uncommitted main.tf/variables.tf diffs left untouched. |
| terraform-cato-bulk-rpf-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured to canonical order (no Sub-policies, flat policy); validated clean against the active dev-override provider build with no `cato_remote_port_fwd_*` vs `cato_remote_port_forward_*` naming conflict surfaced. |
| terraform-cato-bulk-sb-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Rebuilt into canonical heading order incl. a Known Limitations note; no Sub-policies section (flat policy). |
| terraform-cato-bulk-ssia-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Reordered into canonical structure; Sub-policies omitted (none); export command/JSON root (`data.policy.sdpStaticIpAllocation.policy`)/`--module-name` verified against variables.tf/main.tf. |
| terraform-cato-bulk-st-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured incl. new Backup and Restore Workflow + Using Module Outputs sections; a stale untracked `.terraform.lock.hcl` pinned to an unavailable provider version was regenerated so init/validate could run — `versions.tf`'s constraint itself was not touched. |
| terraform-cato-bulk-swp-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured into canonical order; Sub-policies omitted (none); the documented "sections-only, `cato_site_web_proxy_rule` held back pending an upstream API addRule fix" limitation was preserved verbatim in the intro and Importing sections. |
| terraform-cato-bulk-ts-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured incl. a regenerated/trimmed terraform-docs tail matching the if-rules convention; Sub-policies omitted (flat policy); pre-existing uncommitted main.tf/variables.tf diffs left untouched. |
| terraform-cato-bulk-amfh-rules | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured into canonical order (no Sub-policies — confirmed none apply); stale `.terraform.lock.hcl` pinned to an unresolvable provider version was regenerated to validate against the locally available build, satisfying `versions.tf`'s untouched `>= 1.3.1` floor. |
| terraform-cato-bulk-ao-rules | fixed | **copied** | pass | pass | yes | yes | v0.0.1 | Was missing LICENSE — copied byte-for-byte from bulk-if-rules. main.tf/README already correctly use `cato_always_on_rule_bulk` (no stale `cato_bulk_ao_move_rule` references found). Stale `.terraform.lock.hcl` pinned to an unavailable provider version regenerated to let validate run. Upstream repo confirmed to exist via GitHub API — supersedes release-instructions.md's "no origin, create repo" note. |
| terraform-cato-bulk-atr-rules | fixed | **copied** | pass | pass | yes | yes | v0.0.1 | Was missing LICENSE — copied byte-for-byte from bulk-if-rules. Restructured to canonical order incl. a Known Limitations section; no Sub-policies (flat policy). Upstream repo confirmed to exist via GitHub API — supersedes release-instructions.md's "no origin, create repo" note. |
| terraform-cato-bulk-cc-rules | fixed | ok | pass | **fail** | yes | yes | v0.0.1 | Restructured into canonical order (no Sub-policies). `terraform validate` fails against the default-resolved provider 1.4.0 — `cato_client_connectivity_policy`/section resource types are unknown to it; confirmed valid in an isolated test against v1.5.0-prerelease.2. Expected `feat/bulk-policy-families` gating per CLAUDE.md, not a module bug — `versions.tf` left untouched. |
| terraform-cato-bulk-dia-rules | fixed | ok | pass | **fail** | yes | yes | v0.0.1 | Restructured into canonical skeleton; terraform-docs tail refreshed to match actual resources (incl. the `terraform_data.guard_empty_policy` guard implementation). `terraform validate` fails the same way as cc-rules — provider resolves to 1.4.0, which lacks `cato_dynamic_ip_allocation_*` resources; needs v1.5.0-prerelease.2 pinned. Out-of-scope provider-resolution/sequencing issue, not fixed. |
| terraform-cato-bulk-private-access | fixed | ok | pass | pass | yes | yes | v0.0.1 | Restructured into canonical skeleton; added a documented note on the `applications` wrapper-object/flat-list compatibility, grounded in main.tf's own comment (not invented). |
| terraform-cato-socket-bulk-sites | fixed | ok | pass | pass | yes (`allow_empty_sites`) | yes | v0.0.15 | README adapted to sites/interfaces/ranges subject matter (flat JSON, no `data.policy.*`); migration.tf/import-table content preserved verbatim. **Blocker found**: `main.tf` line ~413 still sources its child module via a stray local path, `source = "../terraform-cato-socket"`, with the registry source line (`catonetworks/socket/cato`) commented out next to a `# restore (with bumped version) before publishing` note the module author already left. This must be flipped back to the registry source + a bumped version **before** this module can be published — a local relative-path source will not resolve for registry consumers. Left unfixed since the correct pinned version is a release-sequencing decision, not a formatting one. |

## Remaining for the operator

1. **Fix the socket-bulk-sites blocker** — swap `source = "../terraform-cato-socket"` back to `source = "catonetworks/socket/cato"` with the bumped version, per the module's own inline TODO, before it can be registry-published.
2. **Resolve cc-rules / dia-rules validate failures** by pinning their `versions.tf` to (or testing against) `terraform-provider-cato` v1.5.0-prerelease.2 once that's the standard local/dev provider build — not a module-code problem, a provider-version-resolution one.
3. **Review and commit** the README restructures, the two copied LICENSE files (ao-rules, atr-rules), and any fmt fixes across all 19 clones — everything was deliberately left uncommitted per working convention.
4. **Tag and push**: `v0.0.9` (if-rules), `v0.0.10` (wf-rules), `v0.0.2` (wnw-rules, tls-rules), `v0.0.1` (the 14 never-tagged modules), `v0.0.15` (socket-bulk-sites) — per `docs/release-instructions.md` §2. Note `bulk-if-rules`, `bulk-wf-rules`, `bulk-app-control-rules`, and `bulk-private-access` are on branch `feat/bulk-coverage` and need merging to `main` first.
5. **Terraform Registry add**: for the 14 never-tagged modules, this is their first-ever registry publish — add each repo via the registry UI/GitHub app after tagging (registry picks it up automatically once the tag + app connection exist).
6. **Correct `docs/release-instructions.md` §0** — it claims `ao-rules` and `atr-rules` have no `origin` remote and need a GitHub repo created; this pass confirms both repos already exist (HTTP 200 from the GitHub API) and both local clones' `.git/config` already have the correct `origin` URL. No repo-creation step is actually needed for either.
7. **Push access** — this sandbox has no SSH key loaded, so nothing could be pushed or `ls-remote`'d over SSH during this pass; all upstream-existence checks here were done read-only via the public GitHub API instead.
