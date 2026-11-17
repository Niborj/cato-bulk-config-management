# Provider issue drafts (terraform-provider-cato @ 1.5.0-prerelease.1)

Observed 2026-11-04 against the <source-profile> tenant (<SOURCE_ACCOUNT_ID>) during the 0-sites declarative
brownfield adoption. Three distinct issues; all reproduce with `terraform plan` in
`0-sites/` using `migration.tf` import blocks.

## 1. Refresh hard-errors on sites deleted outside Terraform

**Symptom:** when state tracks a `cato_socket_site` whose site was deleted in CMA,
`terraform plan` fails with `Error: failed to fetch SiteGeneralDetails for site '<id>'`
(API: `Site <id> not found`, path `site.siteGeneralDetails`). Every out-of-band deletion
therefore makes the whole folder unplannable until manual state surgery.

**Expected:** Read should treat a 404/not-found site as removed — return a null state so
Terraform plans a recreate (standard provider behavior for externally-deleted resources).

**Repro evidence:** 0-sites state tracked sites 224579–224582 (deleted); plan errored on
all four. Fixed locally via `terraform state rm` of the four module subtrees
(`terraform.tfstate.pre-surgery-*.bak` retained).

## 2. `cato_network_range` import does not hydrate `site_id` → forced replacement

**Symptom:** after `import` (plain range ID, e.g. `Uzc5NTc4NA==`), state has
`site_id = null`. Any config that sets `site_id` (required in practice — the bulk modules
always set it) plans `+ site_id = "204609" # forces replacement`, i.e. adopting an
existing range immediately wants to destroy and recreate it.

**Expected:** ImportState/Read should populate `site_id` (the entity lookup already knows
the parent site), or `site_id` changes should not force replacement when transitioning
from null-after-import.

## 3. `cato_lan_interface` import does not hydrate `interface_id` → forced replacement

**Symptom:** after `import` (plain entity ID, e.g. `252077`), state has
`interface_id = null`; config's `interface_id = "INT_6"` plans
`+ interface_id = "INT_6" # forces replacement`.

**Expected:** same as #2 — hydrate `interface_id` on import (Read already matches the
entity), or don't force replacement from the null-after-import state.

## Minor (cosmetic) read-back gaps seen in the same plan

- `cato_socket_site.description`: live `null` vs config `""` → perpetual in-place update
  when the export writes `""`. Normalizing read/plan of empty-vs-null would silence it.
- `cato_socket_site.native_range.lag_min_links`: not returned by Read (documented export
  gap) → hand-filled value plans as an in-place add on adoption.

## 4. Policy rule import does not hydrate the `at` ordering block → one-time in-place churn

**Symptom:** after importing a policy rule (observed on `cato_socket_lan_network_rule`,
`cato_socket_lan_firewall_rule`, `cato_application_control_rule`, `cato_always_on_rule`),
state holds `at = { position = "LAST_IN_POLICY", ref = null }` regardless of the rule's
actual placement, so the first plan re-asserts the configured
`{position = LAST_IN_SECTION, ref = <section id>}` as an in-place update on every
imported rule (44 at once on Application Control). Root cause in
`converters_socket_lan_network_rule.go` (`convertReadState3`): Read deliberately carries
forward the *prior* `at` value to avoid drift, but after `ImportStatePassthroughID`
there is no prior value and it defaults to `LAST_IN_POLICY`.

**Expected:** import/first-Read should derive `at` from the rule's actual
section/position, or mark it unknown rather than defaulting — adoption of a large
policy should not plan an update for every rule. (Harmless/idempotent, but it buries
real diffs in noise.)

## 5. Empty-object vs null normalization on nested rule blocks

Example: `cato_socket_bypass_rule` imports with `rule.source = {}` while config maps the
export's empty source to `null` → `- source = {} -> null` in-place change per rule.
Same class as the site `description` ""/null gap. Normalizing empty↔null in Read/plan
would silence it.

## Branch-build verification context (2026-11-04)

The 12 provider-gated reference folders were verified with a local build of
`origin/feat/bulk-policy-families` (479afb6) via dev_overrides — all 12 plan
successfully (results noted per folder README). Reminder for the release: that branch
is 158 commits behind v1.5.0-prerelease.1 (missing wifi_ssid, custom_application,
containers, mailing_list, subscription_group, user, and carrying renames
`cato_remote_port_fwd_*` / `cato_pa_section`) — it must be rebased/merged onto main
before shipping, and the rename-vs-published-name conflicts resolved.

## Current plan result with these issues (for reference)

`Plan: 88 to import, 4 to add, 3 to change, 3 to destroy.` — the 3 destroys/replaces are
exactly issues #2/#3 (2 ranges + 1 LAN interface); the 3 changes are the cosmetic gaps;
1 add is the module's new `terraform_data` guard. With #2/#3 fixed the adoption plan
becomes pure imports + cosmetic touch-ups.
