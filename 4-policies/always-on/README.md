# Always-On (ZTNA)

Manages Cato's ZTNA Always-On policy — rules that control always-on VPN/client enforcement (device posture, bypass behavior) plus sections — as one bulk Terraform-managed unit sourced from a single JSON export. See "What this manages" below for the full picture.

**Current status: blocked.** This folder can't be applied yet — see the next section for why, and check back once a provider release resolves it.

## Blocked on provider release

This family's bulk module (`terraform-cato-bulk-ao-rules`) declares
`cato_bulk_ao_move_rule` — the resource that orders sections/rules and
publishes the policy. That resource exists **only** on the cato provider's
unmerged `feat/bulk-policy-families` branch, not in the published
`1.5.0-prerelease.1` this repo otherwise pins. `terraform init` succeeds;
`terraform validate` fails. Verbatim output captured against this exact
config:

```
$ terraform init -input=false
Initializing the backend...
Initializing modules...
Initializing provider plugins...
Terraform has been successfully initialized!

$ terraform validate
╷
│ Error: Invalid resource type
│
│   on ../../../catonetworks/terraform-cato-bulk-ao-rules/main.tf line 130, in resource "cato_bulk_ao_move_rule" "all_ao_rules":
│  130: resource "cato_bulk_ao_move_rule" "all_ao_rules" {
│
│ The provider catonetworks/cato does not support resource type
│ "cato_bulk_ao_move_rule". Did you mean "cato_bulk_wf_move_rule"?
╵
```

(Terraform's "Did you mean" suggestion points at an unrelated resource —
`cato_bulk_wf_move_rule` is the WAN Firewall move resource, a coincidental
name-similarity match, not a real alternative.) Nothing in this folder can
be `apply`'d until a provider release merges that branch.

## What this manages

Cato's **ZTNA Always-On** policy — rules that control always-on VPN/client
enforcement (action ENFORCE/IGNORE, user bypass allowance, bypass duration,
anti-tamper mode, device posture, platform) plus sections. This folder
manages the entire Always-On policy for the `<target-profile>` account as one bulk
Terraform-managed unit, sourced from a single JSON export.

**Live state:** the `<target-profile>` account currently has **0 sections and 2
rules** (`Always-On Policy`, `Always-on Policy - iOS`) in this policy
family. `migration.tf` in this folder imports both rules; there are no
sections to import.

## Module

[`terraform-cato-bulk-ao-rules`](https://github.com/catonetworks/terraform-cato-bulk-ao-rules) —
sourced from a **local path** (`../../../catonetworks/terraform-cato-bulk-ao-rules`)
instead of the registry, since no registry release exists yet for this
family. A commented-out registry source (`catonetworks/bulk-ao-rules/cato`,
version `0.0.1`) is left in `main.tf` to swap in once published. Under the
hood it creates `cato_always_on_section`, `cato_always_on_rule`, and a
single `cato_bulk_ao_move_rule` resource that encodes rule/section ordering
and publishes the policy — it is this last resource that is missing from
`1.5.0-prerelease.1` (see "Blocked on provider release" above). Unlike
AMFH/Client Connectivity/Dynamic IP Allocation, this module has no
account-level policy-toggle resource.

**Export** runs from the **repo root**. **Import and Terraform commands**
run from **this folder** (`3-policies/always-on/`).

## Authentication

Credentials resolve in precedence order: explicit vars (`terraform.tfvars`) → `CATO_TOKEN`/`CATO_BASEURL` environment variables → a catocli profile (`cato_profile` var or `CATO_PROFILE` env) read from `~/.cato/credentials`. With a profile, no tfvars are needed at all:

```bash
CATO_PROFILE=<target-profile> terraform plan
```

Requires the published provider `2.0.0-prerelease.1` from the Terraform Registry. Verify which tenant a profile targets with `catocli configure show` before planning.

## JSON schema

`../../config_data/all_ao_rules_and_sections.json` follows:

```
data.policy.ztnaAlwaysOn.policy.sections[]           # each: { section_index, section_name }
data.policy.ztnaAlwaysOn.policy.rules[]               # each: { audit, rule: { name, source{user,usersGroup}, platform[], devicePostureProfile[], action (ENFORCE|IGNORE), allowUserBypass, bypassDuration{time,unit}, allowFailOpen, antiTamperMode (OFF|MONITOR|PROTECT), ... }, properties }
data.policy.ztnaAlwaysOn.policy.rules_in_sections[]   # each: { index_in_section, section_name, rule_name }
```

`rules_in_sections` is what actually determines section/rule ordering.

## Export (backup) workflow

Run from the repo root:

```bash
CATO_PROFILE=<source-profile> catocli export ao_rules --json-filename all_ao_rules_and_sections.json --output-directory config_data
```

## Update workflow

1. Edit the JSON directly (or re-export after a Cato UI change).
2. `terraform plan` from this folder — currently blocked, see above.
3. `terraform apply` once the plan matches intent — **blocked until the
   provider ships the bulk-move resource**.
4. Commit the updated JSON alongside the `.tf` changes.

## Brownfield adoption

<details>
<summary>Option A (recommended) — native Terraform import blocks (migration.tf, already written)</summary>

`migration.tf` in this folder imports the 2 live rules by name/id (no
sections exist live, so no section import blocks). It mirrors the module
README's Option A example:

```hcl
locals {
  migrate_json   = jsondecode(file("../../config_data/all_ao_rules_and_sections.json"))
  migrate_policy = local.migrate_json.data.policy.ztnaAlwaysOn.policy
  migrate_rules  = { for rw in local.migrate_policy.rules : rw.rule.name => rw.rule.id }
}

import {
  for_each = local.migrate_rules

  to = module.ao_rules.cato_always_on_rule.rules[each.key]
  id = each.value
}
```

`terraform plan` against this should show `2 to import, 1 to add, 0 to
change, 0 to destroy` once the provider blocker clears — the "1 to add" is
`cato_bulk_ao_move_rule.all_ao_rules`, which cannot be imported (no
`ImportState`, plan-time-only resource). Currently `terraform plan` cannot
even reach that point, since `validate` already fails first — see "Blocked
on provider release".
</details>

<details>
<summary>Option B — catocli import (imperative alternative)</summary>

```bash
catocli import ao_rules_to_tf config_data/all_ao_rules_and_sections.json \
  --module-name module.ao_rules --auto-approve
```

Also blocked today for the same reason — the underlying `terraform import`
calls would hit the same missing-resource-type error on plan/apply of the
move resource.
</details>

## Troubleshooting

- `catocli ping` — verify API connectivity/credentials.
- `catocli configure show` — confirm the active profile's account ID matches
  `account_id_<source-profile>_dev` in `terraform.tfvars`.
- If `terraform validate`/`init` ever **passes** against a future provider
  version, re-run `terraform plan` against `migration.tf` to adopt the 2
  live rules, then delete `migration.tf` and remove the "Blocked on
  provider release" framing from this README.

> **Verified against a local `feat/bulk-policy-families` provider build (2026-11-04):** `terraform plan` succeeds — 2 to import, 2 to add, 2 to change (one-time at-block re-assert). The "blocked" status above applies only to the published 1.5.0-prerelease.1.
