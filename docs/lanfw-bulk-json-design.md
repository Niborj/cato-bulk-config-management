# LAN Firewall (socketLan) bulk JSON design

Schema consumed by `terraform-cato-bulk-lan-fw-rules` and produced by
`catocli export lan_fw_rules` / imported by `catocli import lan_fw_rules_to_tf`.
Companion to `ifw-sub-policy-json-design.md`; differences from the IFW design
are called out explicitly.

## API structure facts (verified live on <test-profile>/<test-profile>, 2026-11-02)

- The LAN policy is **two-level**: sections contain **network rules**
  (`direction`, `transport`, `nat{enabled,natType}`, `site{site,group}`,
  source/destination/service — no action/tracking), and each network rule
  carries its **child firewall rules** inline in `rule.firewall[]`
  (action/tracking/application, source adds `mac`+`vlan`, service adds
  `standard`). There is **no parent-id field** — nesting IS the parent
  reference; child `index` is per-parent; child `section` is null.
- Wrapper-level `ruleType` discriminates `POLICY_RULE` vs `SUB_POLICY_SCOPE`
  (the scope row introducing a sub-policy). Sub-policy membership is the
  wrapper's `subPolicy {id,name}` ref (UI exports alias it as
  `subPolicySocketLanRulePayload`).
- **Sub-policies can own sections**: `section.subPolicyId` links a sub-section
  to its sub-policy, and a sub-policy rule's inner `section` is either that
  sub-section (explicit link — cleaner than IFW) or blanked
  (`{"id":"","name":""}`) when the sub-policy has no sub-section.
- **No cleanup rules and no system rows in the API**: the CMA UI renders
  per-network-rule "Block Any" children and a "Default rules" section, but the
  API does not return them — exports never contain them and nothing needs the
  IFW-style default-section exclusion / re-homing / Corporate Rules synthesis.
  (The exporter still filters `properties[0] == "SYSTEM"` defensively.)
- The catocli-generated socketLan query 500s server-side (`access` on nested
  firewall rules returns null against non-nullable `EntityAccess`); the export
  uses a hand-crafted query that omits `access`/metadata everywhere.
  `service.simple` entries are `{name}` only (no id).
- `strip_ids_recursive` note: refs whose names contain backslashes (e.g.
  network interfaces `Site \ LAN 01`) export as `{id}` instead of `{name}` —
  all consumers accept either (`can(x.name) ? {name} : {id}`).

## Export shape — `data.policy.socketLan.policy`

```jsonc
{
  "enabled": true,
  "sections": [
    { "section_index": 1, "section_name": "Section 1", "section_id": "..." },
    { "section_index": 1, "section_name": "SP1 Sec", "section_id": "...",
      "sub_policy_name": "SP1" }            // sub-section; numbering is parent-scoped
  ],
  "sub_policies": [
    { "sub_policy_name": "SP1", "sub_policy_id": "...", "scope_rule_id": "...",
      "description": "", 
      "enabled": true,        // the sub-policy object's own policy.enabled - NOT settable
                              // via the public API (no enabled on addSubPolicy, no
                              // updateSubPolicy) - informational, same as IFW
      "scope_enabled": true,  // the SUB_POLICY_SCOPE rule's enabled - terraform manages this
      "scope": { "site": {...}, "source": {...}, "destination": {...},
                 "service": {...}, "nat": {...}, "direction": "TO", "transport": "LAN" } }
  ],
  "network_rules": [
    { "audit": {...}, "properties": [],
      "rule": { "id": "...", "name": "...", "description": "", "enabled": true,
                "section": { "name": "..." },   // "" = policy root / sub-policy without sub-section
                "direction": "TO", "transport": "LAN",
                "nat": { "enabled": false, "natType": "DYNAMIC_PAT" },
                "site": {...}, "source": {...}, "destination": {...}, "service": {...} },
      "sub_policy_name": "SP1",                 // present only on sub-policy members
      "firewall_rules": [                        // nested children, ARRAY ORDER = per-parent order
        { "audit": {...}, "properties": [],
          "rule": { "id": "...", "name": "...", "enabled": true, "direction": "TO",
                    "action": "ALLOW", "source": {...+mac,vlan}, "destination": {...},
                    "application": {...}, "service": {simple,standard,custom},
                    "tracking": { "event": {...}, "alert": {...} } } }
      ] }
  ],
  "rules_in_sections": [                         // ordering: sections <-> network rules ONLY
    { "index_in_section": 1, "section_name": "Section 1", "rule_name": "N1" },
    { "index_in_section": 2, "section_name": "Section 1",
      "entry_type": "sub_policy", "sub_policy_name": "SP1" },
    { "index_in_section": 1, "section_name": "SP1 Sec",
      "rule_name": "SP1 N1", "sub_policy_name": "SP1" }
  ]
}
```

Child firewall ordering has no `rules_in_sections` rows — it is the nested
array order (index_in_rule derived as position+1 by the module/import).

## Terraform mapping (module `terraform-cato-bulk-lan-fw-rules`)

| Export object | Resource | Key | Placement |
|---|---|---|---|
| main section | `cato_socket_lan_section.sections` | name | `LAST_IN_POLICY` |
| sub-section | `cato_socket_lan_section.sub_sections` | `<sp>::<name>` | `LAST_IN_POLICY` + `ref = sub-policy id` |
| sub_policies[] | `cato_lf_sub_policy.sub_policies` | name | `LAST_IN_SECTION` of parent section (from its scope row) |
| network rule | `cato_socket_lan_network_rule.network_rules` | name | managed section → `LAST_IN_SECTION`; sub-policy w/o sub-section → `LAST_IN_POLICY` + `ref = sub-policy id`; root → `LAST_IN_POLICY` |
| firewall child | `cato_socket_lan_firewall_rule.firewall_rules` | `<net>::<fw>` | `LAST_IN_RULE` + `ref = parent rule id` |
| ordering | `cato_bulk_lf_move_rule.all_lan_fw_rules` | — | `section_data` + `network_rules` (scope rows merged in, keyed by sub-policy name) + `firewall_rules` |

Rows with `section_name: ""` are excluded from the move resource (it addresses
rules through sections) and surfaced via the `unordered_network_rules` output;
their order is creation order. `""` is the canonical implicit-default-section
sentinel repo-wide (a rule at the policy root, in a section the API never lists
in `policy.sections[]`) — see `docs/ifw-sub-policy-json-design.md` for why it is
the empty string and not `null`.

Import (`catocli import lan_fw_rules_to_tf`): sections → sub-policies →
sub-sections (importable here, unlike IFW) → network rules → firewall rules,
by the ids above; entries with empty ids (greenfield) are skipped; the move
resource is created on first apply.

## Test plan — `module_tests/bulk_lan_fw_rules_sub_policies/`

1. `simple-dev1` (<TEST_ACCOUNT_ID>): brownfield from the live <test-profile> export; import →
   plan (move only) → apply → clean plan.
2. `advanced-dev2` (<TEST_ACCOUNT_ID>): greenfield kitchen sink (every attribute class);
   apply → clean plan → re-export → diff (round-trip stability).
3. `roundtrip-dev3` (11362): greenfield apply → export → reset state →
   import → plan (move only) → apply → clean plan.

All applies with `-parallelism=1` (account-wide reorders are last-writer-wins).
