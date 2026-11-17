# Private Access (PA)

Manages Cato's Private Access policy — a flat, ordered rule list covering access to internally published applications — as one bulk Terraform-managed unit sourced from a single JSON export. See "What this manages" further below for the full picture.

**Current status: blocked** (for a different reason than the other blocked families — see the next section).

## Blocked on provider release — but NOT the way the other five families are

**This is the one family of the six where `terraform validate` unexpectedly
PASSES against the published `1.5.0-prerelease.1` provider.** Both
`cato_private_access_rule` and `cato_private_access_rule_bulk` are already
present in that provider release — they are not gated behind the unmerged
`feat/bulk-policy-families` branch the way the other five families'
move/toggle resources are. Verbatim capture:

```
$ terraform init -input=false
Initializing the backend...
Initializing modules...
Initializing provider plugins...
Terraform has been successfully initialized!

$ terraform validate
Success! The configuration is valid.
```

However, running `terraform plan` with real `<target-profile>` credentials (per
the verification instructions — never applied) hits a **different**
failure: a `locals` evaluation error inside the module itself, caused by a
schema mismatch between what `catocli export pa_rules` actually emits for
the `applications` field and what the module's HCL expects to find there.
Verbatim capture (`TF_VAR_cato_token_<source-profile>_dev` / `TF_VAR_account_id_<source-profile>_dev`
set from the `<source-profile>` catocli profile, `-parallelism=1`, no `-auto-approve`,
never applied):

```
$ terraform plan -input=false -parallelism=1

Terraform used the selected providers to generate the following execution
plan. Resource actions are indicated with the following symbols:
  + create

Terraform planned the following actions, but then encountered a problem:

  # module.pa_rules.terraform_data.guard_empty_policy will be created
  + resource "terraform_data" "guard_empty_policy" {
      + id = (known after apply)
    }

Plan: 1 to add, 0 to change, 0 to destroy.

╷
│ Error: Unsupported attribute
│
│   on ../../../catonetworks/terraform-cato-bulk-private-access/main.tf line 52, in locals:
│   52:           (can(a.application.name) ? { name = a.application.name } : { id = a.application.id })
│
│ This value does not have any attributes.
╵
```

### Root cause

The module's `rule_bodies` local (`terraform-cato-bulk-private-access/main.tf`
line ~49-53) expects each rule's `applications` field to be a **list** of
wrapper objects, each shaped `{ application: { name | id } }`:

```hcl
applications = [
  for a in try(rw.rule.applications, []) :
  (can(a.application.name) ? { name = a.application.name } : { id = a.application.id })
]
```

But the live export (`../../config_data/all_pa_rules_and_sections.json`,
produced by `catocli export pa_rules`) instead emits `applications` as a
**single object** containing one array under the key `"application"` — e.g.
for rule `Beta User Access - OS Validation`:

```json
"applications": { "application": [ { "name": "WIKI" } ] }
```

So `try(rw.rule.applications, [])` returns that one object (not a list),
the `for` loop iterates its single value (the `[{name:"WIKI"}]` array
itself, as the sole "element" of a one-key object), and `a.application`
fails because that iterated value has no `application` attribute. This is
a genuine schema mismatch between this module and this exporter's actual
output shape for `pa_rules` — not something fixable from this folder's
`.tf` files (the module source lives in a sibling clone,
`<path-to-local-module-clone>/terraform-cato-bulk-private-access`,
outside this repo). Flag it upstream (module or exporter) rather than
patching around it here.

Net effect: this folder is still **blocked end-to-end**, just not for the
reason the other five are. Do not `apply`.

## What this manages

Cato's **Private Access** policy — a flat, ordered rule list (no sections
yet in the terraform surface, no sub-policies) covering action,
applications, source (users/user_groups), connection_origins, countries,
devices, platforms, schedule, active_period, tracking, and
user_attributes.risk_score. This folder manages the entire Private Access
policy for the `<target-profile>` account as one bulk Terraform-managed unit,
sourced from a single JSON export.

**Live state:** the `<target-profile>` account currently has **3 rules** (`Beta
User Access - OS Validation`, `Block Risky Access to Corporate
Applictions`, `Access to Corporate Application`) and no sections (this
policy type has none in the terraform surface). `migration.tf` in this
folder imports all 3 rules by name/id.

## Module

[`terraform-cato-bulk-private-access`](https://github.com/catonetworks/terraform-cato-bulk-private-access) —
sourced from a **local path** (`../../../catonetworks/terraform-cato-bulk-private-access`)
instead of the registry, since no registry release exists yet for this
family (kept consistent with the other five new folders even though this
one's resources already validate — see commented-out registry source,
`catonetworks/bulk-private-access/cato` version `0.0.1`, in `main.tf`).
Under the hood it creates `cato_private_access_rule` (full attribute
coverage, publishes per rule) and a single
`cato_private_access_rule_bulk` resource that encodes rule ordering and
republishes on changes. The account-level policy toggle
(`cato_private_access_policy`) is intentionally **not** managed by this
module — enable the policy separately (CMA or a standalone resource) if
enforcement is wanted.

**Export** runs from the **repo root**. **Import and Terraform commands**
run from **this folder** (`3-policies/private-access/`).

## Authentication

Credentials resolve in precedence order: explicit vars (`terraform.tfvars`) → `CATO_TOKEN`/`CATO_BASEURL` environment variables → a catocli profile (`cato_profile` var or `CATO_PROFILE` env) read from `~/.cato/credentials`. With a profile, no tfvars are needed at all:

```bash
CATO_PROFILE=<target-profile> terraform plan
```

Requires the published provider `2.0.0-prerelease.1` from the Terraform Registry. Verify which tenant a profile targets with `catocli configure show` before planning.

## JSON schema

`../../config_data/all_pa_rules_and_sections.json` follows:

```
data.policy.privateAccess.policy.rules[]            # each: { audit, rule: { name, action.action, applications: { application[] }, source{user,usersGroup}, connectionsOriginList, country, device, platform, schedule, userAttributes.riskScore, ... }, properties }
data.policy.privateAccess.policy.rules_in_policy[]   # each: { index_in_policy, rule_name } — THE ordering table (no sections for this type)
```

Note the `applications` shape documented above under "Root cause" — the
module's name-mapping comment (`applications[].application` →
`applications`) assumes a list-of-wrappers shape; the live exporter output
is a single wrapper object around one array instead.

## Export (backup) workflow

Run from the repo root:

```bash
CATO_PROFILE=<source-profile> catocli export pa_rules --json-filename all_pa_rules_and_sections.json --output-directory config_data
```

## Update workflow

1. Edit the JSON directly (or re-export after a Cato UI change).
2. `terraform plan` from this folder — currently blocked by the
   `applications` schema mismatch above, not by a missing resource type.
3. `terraform apply` — **blocked** until the module/exporter mismatch is
   resolved upstream.
4. Commit the updated JSON alongside the `.tf` changes.

## Brownfield adoption

<details>
<summary>Option A (recommended) — native Terraform import blocks (migration.tf, already written)</summary>

`migration.tf` in this folder imports the 3 live rules by name/id. This
module has no section resources (Private Access has none yet) and no
policy-toggle resource wired in, so `cato_private_access_rule` is the only
importable resource:

```hcl
locals {
  migrate_json  = jsondecode(file("../../config_data/all_pa_rules_and_sections.json"))
  migrate_rules = { for rw in local.migrate_json.data.policy.privateAccess.policy.rules : rw.rule.name => rw.rule.id }
}

import {
  for_each = local.migrate_rules

  to = module.pa_rules.cato_private_access_rule.rules[each.key]
  id = each.value
}
```

In principle `terraform plan` against this should show `3 to import, 1 to
add, 0 to change, 0 to destroy` (the "1 to add" being
`cato_private_access_rule_bulk.all_pa_rules`, which cannot be imported).
In practice, today, `plan` cannot reach that point at all — it fails first
on the `applications` locals bug documented above, regardless of whether
`migration.tf` is present.
</details>

<details>
<summary>Option B — catocli import (imperative alternative)</summary>

```bash
catocli import pa_rules_to_tf config_data/all_pa_rules_and_sections.json \
  --module-name module.pa_rules --auto-approve
```

Also blocked today for the same reason — the underlying `terraform
import`/plan evaluation hits the same `applications` locals error.
</details>

## Known limitations

- `applications` schema mismatch — see "Root cause" above. This blocks
  every plan/apply against the live export until fixed upstream.
- `source.systemGroup` appears in exports for fidelity but has no
  terraform attribute yet.
- `active_period` is only passed when meaningful.
- Run applies with `-parallelism=1` when several module instances share an
  account.

## Troubleshooting

- `catocli ping` — verify API connectivity/credentials.
- `catocli configure show` — confirm the active profile's account ID
  matches `account_id_<source-profile>_dev` in `terraform.tfvars`.
- If `terraform plan` ever gets past the `applications` error (module
  fixed upstream, or exporter changed to emit a flat list), re-run this
  folder's verification (`terraform plan -parallelism=1` with real
  creds, never apply) and update this README's "Blocked on provider
  release" framing accordingly — this family may ship well before the
  other five, since its core resources already validate.
