# Design: sub-policy support in the bulk Internet Firewall JSON schema

**Status: proposed design — not yet implemented** in the module
(`catonetworks/terraform-cato-bulk-if-rules`) or the CLI (`catocli export if_rules` /
`catocli import if_rules_to_tf`). This doc is the specification both implementations
build against. Companion test scaffolding lives in
`terraform-provider-cato-tests/module_tests/bulk_if_rules_sub_policies/`.

## Problem

Cato Internet Firewall now supports **sub-policies**: a special row inside a section
(`ruleType: SUB_POLICY_SCOPE`, `action: SUB_POLICY`) that scopes a nested policy with
its own rules and, optionally, its own sub-sections. The entire bulk pipeline —
export JSON shape, Terraform module, brownfield import — is flat today and has zero
sub-policy awareness. We need a new JSON contract that:

1. Is **backward compatible**: existing flat configs (sections + rules +
   `rules_in_sections`) keep working with no changes and no state migration.
2. Supports all three shapes: **only sections**, **only sub-policies**, and
   **sub-policies combined with sub-sections**.
3. Controls ordering at every level: sections in the policy, sub-policies among
   sibling rules in a section, sub-sections within a sub-policy, rules within any
   section/sub-policy.
4. Round-trips through the CLI: `catocli export if_rules` produces it; greenfield
   `terraform apply` creates from it; brownfield `catocli import if_rules_to_tf`
   imports existing resources **by id** over live state.

## Verified source data (live API, <test-profile> profile, account <TEST_ACCOUNT_ID>)

The raw GraphQL response (`config_data/fixtures/example_ifw_<test-profile>.json` = flat account,
`config_data/fixtures/example_ifw_<test-profile>.json` = sub-policy account; file 1 is a strict structural subset
of file 2) represents sub-policies as:

- `policy.rules[]` is **one flat array** in exact UI/execution order, interleaving
  main rules, scope rows, and sub-rules. Discriminators per entry:
  - `ruleType`: `POLICY_RULE` | `SUB_POLICY_SCOPE` (enum also defines `SUB_RULE`;
    live data returns sub-rules as `POLICY_RULE` with the parent link set)
  - `subPolicy { id name }` — null for main-policy rules; set on scope rows AND on
    every rule belonging to that sub-policy (the real parent link). NOTE: the raw
    UI-export files show this field under an alias
    (`subPolicyInternetFirewallRulePayload`); the real schema field is `subPolicy`.
  - scope rows additionally have `rule.action: "SUB_POLICY"` and carry normal
    matching criteria (source, etc.) — the scope's match condition.
- `rule.index` is **scope-relative**: main-policy rows number 1..N with scope rows
  consuming a slot; each sub-policy's rules independently restart at 1. The UI's
  dotted numbering (3.1, 3.2) is derived, not stored.
- `policy.sections[]` holds main sections AND sub-sections in one flat array;
  a sub-section is distinguished by non-null `section.subPolicyId`.
- Sub-policy rules **without** an explicit sub-section carry an empty section ref
  (`{"id": "", "name": "", "subPolicyId": null}`) and rely purely on `subPolicy` for
  ownership. (Live <test-profile>: Sub Policy 1 has a sub-section; Sub Policies 2 and 3
  don't.)
- `section_name: ""` is also the **general implicit-default-section sentinel** for
  flat (non-sub-policy) policies across every family: a rule at the policy root,
  in a section the API never lists in `policy.sections[]`. The empty string — NOT
  `null` — is canonical because the composite state key
  `"<sub_policy>::<section>"` must render identically in the Python importer
  (f-string) and HCL interpolation; `null` renders `"None"` vs `""` respectively,
  silently breaking brownfield import address matching. (Exports before 2026-11
  synthesized the literal `"Default Section"` on some flat paths — removed; that
  string never came from the API and is treated as a legacy equivalent of `""`.)
- `policy.subPolicies[]` is metadata only: `{id, name, description, enabled,
  policyLevel: "SUB_POLICY"}` per sub-policy. No nested rules/sections inside it.
- All policy-structure ids (policy, rule, section, sub-policy) are UUIDv4.

Provider support (confirmed in the local `terraform-provider-cato` clone, landed
after v0.0.91):

- `cato_if_sub_policy` — name/description/`at` (all RequiresReplace), computed
  `scope_rule_id`, `scope` block reusing the rule matcher schema.
- `cato_if_rule.sub_policy_id` — immutable; when set, the rule is created inside
  that sub-policy (positioned before its cleanup rule).
- **Gap:** `cato_bulk_if_move_rule` does not yet order rules *inside* sub-policies —
  its reorder input's `SubRules` is always built empty, and rules with an empty
  SectionID are skipped from main-policy reordering. The LAN Firewall bulk-move
  resource (`cato_bulk_lf_move_rule`) is the mature precedent to mirror when
  extending it: `rule_type` enum, `section_key` disambiguator, `sub_policy_name` on
  section entries.

## The new JSON schema

Root unchanged: `data.policy.internetFirewall.policy`. All new keys are **additive
and optional** — a legacy flat file is a valid instance of this schema.

```jsonc
{
  "data": { "policy": { "internetFirewall": { "policy": {

    // ───────────────────────────────────────────────────────────────────────
    // sections[]: main sections AND sub-sections, one flat array.
    //   sub_policy_name absent/null  => main-policy section (legacy shape, unchanged)
    //   sub_policy_name set          => sub-section owned by that sub-policy
    //   section_index = order WITHIN ITS PARENT (the main policy, or the owning
    //                   sub-policy) — parent-scoped, so numbering restarts per parent.
    // EXPORT INVARIANT: the account's built-in default section ("Default
    // Outbound Internet", or its renamed equivalent — always the FIRST section
    // the API returns) is NEVER emitted: it cannot be created/managed by the
    // bulk module (same spirit as excluding SYSTEM rules like "Block any P2P").
    // Any non-SYSTEM rules or sub-policy scope rows that live in it are
    // re-homed to the next section down (first non-default main section),
    // numbered ahead of that section's own rules so policy order is preserved
    // — e.g. 4 user rules under Block any P2P become rows 1-4 of the next
    // section, leaving Block any P2P alone in the default.
    // SINGLE-SECTION TENANTS (new deployments): with no next section to
    // re-home into, the export synthesizes a managed section named
    // "Corporate Rules" with section_id "" (it doesn't exist live; terraform
    // creates it on first apply, moves the rules there, and later re-exports
    // see it as a normal top section — round-trip stable). A pristine tenant
    // (only SYSTEM rules) synthesizes nothing and exports empty.
    // import_rules_to_tf skips sections with empty section_id.
    // ───────────────────────────────────────────────────────────────────────
    "sections": [
      { "section_index": 1, "section_name": "Section 1",                 "section_id": "92c35dcd-..." },
      { "section_index": 2, "section_name": "Section 2",                 "section_id": "9df0a849-..." },
      { "section_index": 1, "section_name": "Section 1 Sub Policy 1 Sub Section 1",
        "section_id": "e336a3be-...", "sub_policy_name": "Section 1 Sub Policy 1" }
    ],

    // ───────────────────────────────────────────────────────────────────────
    // sub_policies[] (NEW): one entry per sub-policy; maps 1:1 to the
    // cato_if_sub_policy resource. The SUB_POLICY_SCOPE row does NOT appear in
    // rules[] — it is not a cato_if_rule; its matching criteria live in scope{},
    // and its position among sibling rules lives in rules_in_sections
    // (entry_type: "sub_policy").
    //   *_id fields: brownfield import keys; ignored on greenfield apply.
    // CLEANUP RULE: addSubPolicy auto-creates a "<name> - Cleanup Rule" inside
    // every sub-policy (verified live). It is embedded in the sub-policy — the
    // cato_if_sub_policy resource creates/updates/imports/destroys it with the
    // sub-policy (like a socket site's default interface), exposing computed
    // cleanup_rule_id and an optional cleanup_rule block (its name IS
    // customizable). Identity is POSITIONAL: the cleanup is always the
    // sub-policy's LAST rule (max rule.index) — renames and manual moves are
    // re-resolved on every read/update/import. Exports therefore emit it
    // nested as sub_policies[].cleanup_rule (full {audit, rule, properties}
    // entry), never in rules[]/rules_in_sections, and the import tool needs no
    // separate import step: `terraform import` of the sub-policy id pulls the
    // cleanup into state via the provider's last-rule lookup. Creating it as a
    // plain cato_if_rule would always fail with ruleNameAlreadyExists.
    // ───────────────────────────────────────────────────────────────────────
    "sub_policies": [
      { "sub_policy_name": "Section 1 Sub Policy 1",
        "sub_policy_id":  "1f4f86c9-6ee5-488a-8f00-dd42ffb18101",
        "scope_rule_id":  "e8225353-19c3-45bd-b2c6-f2d4d33d8349",
        "description": "", "enabled": false,
        "scope": { "source": { "ip": ["1.2.3.4"] }, "connectionOrigin": "ANY" } },
      { "sub_policy_name": "Section 1 Sub Policy 2",
        "sub_policy_id":  "08bcd3df-...", "scope_rule_id": "...",
        "description": "", "enabled": false,
        "scope": { "source": { "group": [{ "name": "my group name" }] } } },
      { "sub_policy_name": "Section 2 Sub Policy 1",
        "sub_policy_id":  "719150d2-...", "scope_rule_id": "...",
        "description": "", "enabled": false,
        "scope": { "source": { "group": [{ "name": "my group name" }] } } }
    ],

    // ───────────────────────────────────────────────────────────────────────
    // rules[]: flat, exactly as today — now also carrying sub-rules.
    //   sub_policy_name absent/null => main-policy rule (legacy shape, unchanged)
    //   sub_policy_name set         => rule lives inside that sub-policy
    //   rule.section.name: the containing section; for sub-rules either the
    //   sub-section's name, or "" when the sub-policy has no explicit sub-section.
    //   rule.id retained for brownfield import.
    // ───────────────────────────────────────────────────────────────────────
    "rules": [
      { "audit": { "...": "..." },
        "rule": { "id": "44928cc2-...", "name": "Section 1 Rule 1",
                  "section": { "name": "Section 1" },
                  "action": "BLOCK", "...": "..." },
        "properties": [] },
      { "audit": { "...": "..." },
        "rule": { "id": "274063d5-...",
                  "name": "Section 1 Sub Policy 1 Sub Section 1 Sub Rule 1",
                  "section": { "name": "Section 1 Sub Policy 1 Sub Section 1" },
                  "action": "BLOCK", "...": "..." },
        "properties": [],
        "sub_policy_name": "Section 1 Sub Policy 1" },
      { "audit": { "...": "..." },
        "rule": { "id": "....", "name": "Section 1 Sub Policy 2 Sub Rule 1",
                  "section": { "name": "" },
                  "action": "BLOCK", "...": "..." },
        "properties": [],
        "sub_policy_name": "Section 1 Sub Policy 2" }
    ],

    // ───────────────────────────────────────────────────────────────────────
    // rules_in_sections[]: THE ordering table (array order is irrelevant —
    // ordering always derives from the tuple fields), extended two ways:
    //   entry_type:       "rule" (default when absent — every legacy row) |
    //                     "sub_policy" (this slot is a sub-policy's scope row)
    //   sub_policy_name:  on a rule row => this row orders a rule INSIDE that
    //                     sub-policy; combined with section_name "" for
    //                     sub-policies without explicit sub-sections.
    // ───────────────────────────────────────────────────────────────────────
    "rules_in_sections": [
      // (no row for the default section or its SYSTEM rules — see the
      //  export invariant on sections[] above)
      { "index_in_section": 1, "section_name": "Section 1", "rule_name": "Section 1 Rule 1" },
      { "index_in_section": 2, "section_name": "Section 1",
        "entry_type": "sub_policy", "sub_policy_name": "Section 1 Sub Policy 1" },
      { "index_in_section": 3, "section_name": "Section 1",
        "entry_type": "sub_policy", "sub_policy_name": "Section 1 Sub Policy 2" },
      { "index_in_section": 4, "section_name": "Section 1", "rule_name": "Section 1 Rule 2" },

      { "index_in_section": 1, "section_name": "Section 1 Sub Policy 1 Sub Section 1",
        "rule_name": "Section 1 Sub Policy 1 Sub Section 1 Sub Rule 1",
        "sub_policy_name": "Section 1 Sub Policy 1" },
      { "index_in_section": 2, "section_name": "Section 1 Sub Policy 1 Sub Section 1",
        "rule_name": "Sub Policy 2 - Cleanup Rule",
        "sub_policy_name": "Section 1 Sub Policy 1" },

      { "index_in_section": 1, "section_name": "",
        "rule_name": "Section 1 Sub Policy 2 Sub Rule 1",
        "sub_policy_name": "Section 1 Sub Policy 2" },
      { "index_in_section": 2, "section_name": "",
        "rule_name": "Section 1 Sub Policy 2 - Cleanup Rule",
        "sub_policy_name": "Section 1 Sub Policy 2" },

      { "index_in_section": 1, "section_name": "Section 2",
        "entry_type": "sub_policy", "sub_policy_name": "Section 2 Sub Policy 1" },
      { "index_in_section": 1, "section_name": "",
        "rule_name": "US Sub Policy - Cleanup Rule",
        "sub_policy_name": "Section 2 Sub Policy 1" },
      { "index_in_section": 2, "section_name": "",
        "rule_name": "Section 2 Sub Policy 2 Rule 1",
        "sub_policy_name": "Section 2 Sub Policy 1" }
    ]
  }}}}
}
```

The `rules_in_sections` block above is the **complete worked ordering example** for
the live <test-profile> policy — it reconstructs the UI numbering 1, 2, 3, 3.1, 3.2, 4, 4.1,
4.2, 5, 6, 6.1, 6.2 deterministically.

## Design rules

### Backward compatibility (requirement 1)

Compatibility is **by absence**. A legacy file has no `sub_policies` key, no
`entry_type` on any ordering row, and no `sub_policy_name` anywhere. Consumers
default accordingly:

| Missing thing | Interpreted as |
|---|---|
| `sub_policies` key | `[]` — no sub-policies |
| `entry_type` on a `rules_in_sections` row | `"rule"` |
| `sub_policy_name` on a rule / section / ordering row | main policy |

The module reads everything through `try(..., null)` guards, so a legacy file
produces exactly today's resource set at exactly today's addresses
(`cato_if_section.sections["<name>"]`, `cato_if_rule.rules["<name>"]`) — **no state
migration for existing deployments**.

### Scenario coverage (requirement 2)

- **Only sections**: the legacy shape, unchanged (or the new shape with
  `sub_policies: []`).
- **Only sub-policies**: no custom sections; `sub_policies[]` entries with NO
  `entry_type: "sub_policy"` rows at all (exports never emit the default section
  or rows referencing it; with no managed section to order within, the module
  places sub-policies LAST_IN_POLICY and surfaces them via the
  `unordered_sub_policies` output); their rules ordered with
  `section_name: ""` + `sub_policy_name`.
- **Sub-policies with sub-sections**: all keys in play, as in the worked example. A
  single file can freely mix all three per section.

### Ordering (requirement 3)

One uniform rule: **order = (parent scope, section_index, index_in_section)**, never
array position.

- **Sections** in the main policy: `section_index` among entries with no
  `sub_policy_name`.
- **Sub-policies** among sibling rules: their `entry_type: "sub_policy"` row's
  `index_in_section` within the parent section — scope rows consume an ordinary rule
  slot, exactly as the live API numbers them.
- **Sub-sections** within a sub-policy: `section_index` among entries sharing that
  `sub_policy_name`.
- **Rules** anywhere: `index_in_section` within their `(sub_policy_name ?? "",
  section_name)` group.

### Identity & uniqueness

- Rule names and sub-policy names: globally unique (existing module constraint,
  unchanged — names are the Terraform for_each keys and the move-resource's lookup
  keys against the live policy).
- Section identity: the composite `(sub_policy_name ?? "", section_name)`. Two
  different sub-policies MAY each have a sub-section with the same name; consumers
  must key maps by the composite (module key format: `"<sub_policy_name>::<section_name>"`),
  mirroring the LAN FW provider's `section_key` precedent.

### Brownfield import-by-id (requirement 4)

Every object carries its live id — inert for greenfield `apply`, consumed by
`catocli import if_rules_to_tf`:

| Object | id field | Imported into |
|---|---|---|
| Main section | `sections[].section_id` | `cato_if_section.sections["<name>"]` |
| Sub-section | `sections[].section_id` | `cato_if_section.sub_sections["<sp>::<name>"]` |
| Sub-policy | `sub_policies[].sub_policy_id` | `cato_if_sub_policy.sub_policies["<name>"]` |
| Main rule | `rules[].rule.id` | `cato_if_rule.rules["<name>"]` |
| Sub-rule | `rules[].rule.id` | `cato_if_rule.sub_rules["<sp>::<name>"]` |

(`scope_rule_id` is informational — the scope row is managed by the
`cato_if_sub_policy` resource, which computes it.)

## Module changes (terraform-cato-bulk-if-rules)

- Bump `required_providers` to the first release containing `cato_if_sub_policy` /
  `cato_if_rule.sub_policy_id` (> 0.0.91; module is currently pinned 0.0.65).
- Locals split with `try()` guards: `main_sections`, `sub_sections`, `main_rules`,
  `sub_rules`, `sub_policies` (default `[]` / filtered on `sub_policy_name`).
- Resources:
  - `cato_if_section.sections` — main sections only; **unchanged keys/behavior**.
  - `cato_if_rule.rules` — main rules only; **unchanged keys/behavior**.
  - `cato_if_sub_policy.sub_policies` (new) — keyed by `sub_policy_name`; `scope`
    from the entry; `at` derived from its ordering row.
  - `cato_if_section.sub_sections` (new) — keyed `"<sp>::<name>"`. ⚠️ Verify the
    provider's section resource supports sub-policy parenting; if it doesn't yet,
    this is a provider work item and sub-sections are export/import-only until then.
  - `cato_if_rule.sub_rules` (new) — keyed `"<sp>::<name>"`, sets
    `sub_policy_id = cato_if_sub_policy.sub_policies[each.value.sub_policy_name].id`.
  - `cato_bulk_if_move_rule.all_if_rules` — main-policy rows only (rule rows +
    scope rows). ⚠️ **Provider gap:** ordering *inside* sub-policies isn't wired
    into the reorder input yet (`SubRules` always empty). Near-term mitigation:
    create-order via `at` refs on `sub_rules`. Proper fix: extend the resource's
    schema following `cato_bulk_lf_move_rule` (`rule_type`, `section_key`,
    `sub_policy_name`).
- Outputs: add `sub_policy_ids`, `sub_section_ids`, `sub_rule_ids` maps alongside
  the existing flat ones.

## CLI changes (cato-cli)

### `catocli export if_rules` (`export_rules.py` → `export_if_rules_to_json`)

- GraphQL query additions (field names verified live — the UI-export files use
  aliases): `ruleType`, `subPolicy { id name }`, `section { id name subPolicyId }`,
  and top-level `subPolicies { policy { id name description enabled policyLevel } }`.
- Transform changes:
  1. Rows with `ruleType == "SUB_POLICY_SCOPE"` are **removed from `rules[]`** and
     emitted as `sub_policies[]` entries (name, description, ids, and the scope
     row's matching criteria → `scope{}`), joined with the top-level `subPolicies[]`
     metadata (`enabled`).
  2. Rules with `subPolicy != null` get `"sub_policy_name"` on their `rules[]` entry.
  3. Ordering counters keyed by `(sub_policy_name ?? "", section_name)` instead of
     bare section name (this also fixes the pre-existing same-name section collision
     bug in the flat exporter). Scope rows emit `entry_type: "sub_policy"` rows at
     their position in the parent section.
  4. Sections with `subPolicyId != null` exported with `sub_policy_name` and a
     per-parent `section_index`.
  5. Flat accounts produce the same shape as today plus `"sub_policies": []` — a
     structural no-op for the module.

### `catocli import if_rules_to_tf` (`import_rules_to_tf.py`)

- Extraction walks all three arrays per the import table above (today it reads only
  top-level `rules[]`/`sections[]` and would silently drop everything nested).
- Import order: main sections → sub_policies → sub_sections → main rules → sub_rules.
- Also fix the if_rules path's missing `module.` auto-prefix on `--module-name`
  (already present in the wf/wnw/tls variants).

## Test plan (terraform-provider-cato-tests/module_tests/bulk_if_rules_sub_policies)

**Built** (fixtures + configs; they gate the not-yet-implemented module/CLI changes).
Three per-account folders, following the `bulk_ranges/` convention (folder-local
`main.tf`/`variables.tf`/`versions.tf` + fixture JSONs, module `source` pointing at
the local `terraform-cato-bulk-if-rules` clone). Credentials via
`TF_VAR_cato_token_dev{1,2,3}` / `TF_VAR_account_id_dev{1,2,3}` environment
variables.

| Folder | Account | Scenario |
|---|---|---|
| `brownfield-flat-dev1/` | <test-profile> (<TEST_ACCOUNT_ID>) | `ifw_flat_legacy.json` — real `catocli export if_rules` capture (legacy shape, live ids). Backward-compat gate + import-by-id over existing dev1 resources. |
| `brownfield-subpolicies-dev2/` | <test-profile> (<TEST_ACCOUNT_ID>) | `ifw_sub_policies_with_sub_sections.json` — the live sub-policy policy in the new schema (generated by the scripted transform in this doc's CLI spec; real ids). Import-by-id of sub-policies/sub-sections/sub-rules over existing dev2 resources. |
| `greenfield-dev3/` | <test-profile> (creds to be provided) | Four from-scratch fixtures: `ifw_sections_only.json`, `ifw_sub_policies_only.json`, `ifw_combined.json`, `ifw_ordering_stress.json` (shuffled arrays + same-named sub-sections in two sub-policies — composite-key collision check). |

Assertions: expected counts via `deployment_summary`/`parsed_configuration` outputs,
`sub_policy_ids`/`sub_rule_ids` populated where expected, idempotence (post-apply
plan is empty), and for the brownfield folders a clean plan immediately after
`catocli import if_rules_to_tf`. See that folder's README for run instructions.

---

## WAN Firewall parity (2026-11-02)

The same design has been ported 1:1 to the WAN Firewall:

- Provider: `cato_wf_sub_policy` regenerated from the fixed `cato_if_sub_policy`
  (description ""-round-trip, input-only `at`, embedded `cleanup_rule` with
  positional last-rule identity + customizable name). `cato_wf_rule` gained the
  same adopt-on-collision Create + transient-read retry; `cato_bulk_wf_move_rule`
  gained the re-snapshot/rebuild reorder retry.
- CLI: `catocli export wf_rules` emits the identical JSON contract under
  `data.policy.wanFirewall.policy` (sub_policies[], cleanup_rule nesting,
  scope/scope_enabled split, sub-sections, SUB_POLICY_SCOPE exclusion from
  rules[]); `catocli import wf_rules_to_tf` is sub-policy aware
  (cato_wf_section/cato_wf_sub_policy/cato_wf_rule + sub_rules addresses).
- Module: `terraform-cato-bulk-wf-rules` has the same locals split, resources,
  move-row remapping and outputs as `terraform-cato-bulk-if-rules`.

**One intentional difference:** WAN Firewall has no API-default section
("Default Outbound Internet" is IFW-only), so the WF export performs **no
section exclusion, no rule re-homing, and no "Corporate Rules" synthesis** —
that machinery exists only in the IFW exporter.

---

## Full policy-family coverage (2026-11-03)

The nine previously uncovered `PolicyMutations` families now have complete
bulk stacks (provider resources + catocli export/import + terraform-cato-bulk
module + module_tests scenarios), all built on cato-go-sdk v1.1.4 with no SDK
modifications:

| Family | Provider prefix | catocli | Module |
|---|---|---|---|
| terminalServer | cato_terminal_server_* | ts_rules | terraform-cato-bulk-ts-rules |
| antiMalwareFileHash | cato_anti_malware_file_hash_* | amfh_rules | ...-bulk-amfh-rules |
| sdpStaticIpAllocation | cato_sdp_static_ip_allocation_* | ssia_rules | ...-bulk-ssia-rules |
| dynamicIpAllocation | cato_dynamic_ip_allocation_* | dia_rules | ...-bulk-dia-rules |
| clientConnectivity | cato_client_connectivity_* | cc_rules | ...-bulk-cc-rules |
| splitTunnel | cato_split_tunnel_* | st_rules | ...-bulk-st-rules |
| remotePortFwd | cato_remote_port_fwd_* | rpf_rules | ...-bulk-rpf-rules |
| siteWebProxy | cato_site_web_proxy_* | swp_rules | ...-bulk-swp-rules |
| socketBypass | cato_socket_bypass_* | sb_rules | ...-bulk-sb-rules |

Every family: rule + section + policy-toggle resources and a sequential
`cato_bulk_<abbr>_move_rule` mover (BuildFlatMoveSequence; socketBypass's
API-side reorderPolicy left as a future optimization). Each module accepts
`policy_enabled` because all nine features ship disabled on new tenants.

Partial gaps also closed: `cato_pa_section` (Private Access sections) and
policy enable/disable toggles for appTenantRestriction, ztnaAlwaysOn and
wanNetwork. **Known gap:** no TLS Inspect toggle - the SDK has no TLS read
query operation (mutations only), and SDK changes are out of scope.
