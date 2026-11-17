# Project Update: Cato Tenant Configuration Replication — End-to-End Flow Complete

**TL;DR:** The reference architecture can now export, version, validate, adopt (brownfield),
and replicate an entire Cato tenant's configuration — sites, interfaces, Wi-Fi SSIDs,
static hosts, all dependency objects, and every policy family — using native Terraform
resources driven by `catocli` JSON exports. Everything is name-keyed and name-referenced
for cross-tenant portability, every object type has a declarative import path, and the
whole flow was verified live against the <source-profile> tenant.

## What the end-to-end flow looks like now

1. **Export** — `catocli export <type>` covers 13+ object types (sites + 4 policy
   families + groups, global IP ranges, custom applications, FQDN containers, IP-range
   containers, mailing lists, subscription groups, static hosts). Exports are
   deterministic (stable sorting → clean diffs), support `--names` filters for selective
   pulls (with escaping for names containing commas), `--merge-into` to upsert objects
   from another tenant into an existing dataset, and `--strip-ids` for cross-tenant
   migration. Static hosts also export as CSV for bulk human editing.
2. **Version** — exports live in git (`config_data/`) as the diffable, restorable
   backup-of-record. A webhook-triggered GitHub Action captures unreviewed exports to a
   separate directory and opens a PR, so a human always arbitrates drift.
3. **Validate** — `scripts/validate_config_data.py` lints every export (shape, unique
   names, rule/section ordering references, cross-file name integrity, import-id
   presence) and runs in CI alongside `terraform fmt`/`validate`. Every bulk module now
   carries an `allow_empty_policy` guard so a truncated or wrong-tenant export can
   never mass-destroy a live policy.
4. **Adopt (brownfield)** — native Terraform `import` blocks (a one-shot `migration.tf`)
   adopt live objects declaratively, previewable before any state change. Verified live:
   `1-dependencies` plans **26 imports, 0 add, 0 change, 0 destroy** across 7 object
   types; `0-sites` plans **88 imports** covering sites, WAN/LAN interfaces, LAG
   members, network ranges, Wi-Fi SSIDs, and 78 static hosts. The imperative
   `catocli import <type>_to_tf` flow remains documented as the alternative everywhere.
5. **Replicate** — `catocli export all --strip-ids --no-account-suffix --output-directory <dir>`
   (requires the catocli release that ships `export all --strip-ids`, superseding the
   now-removed `scripts/migrate_tenant.py`) produces a stripped, portable export set
   plus a generated `CHECKLIST.md` of the few manual re-resolutions (id-only group
   member types, mailing-list admin IDs, Wi-Fi PSKs, licenses, socket registration).
   Because all state addressing and references are by **name**, the same JSON applies
   greenfield to a new tenant.

## Highlights from this phase

- **Native everything**: the catocli/python custom-application wrapper is gone — all
  seven dependency types are plain provider resources on v1.5.0-prerelease.1.
- **Wi-Fi SSIDs** are now first-class across the whole stack: provider resource → socket
  modules (`wifi_ssids` + sensitive passkey vars) → export → import → live-verified
  against two real SSIDs.
- **Static hosts** managed at scale (78 live hosts adopted declaratively) with a
  JSON-for-Terraform / CSV-for-humans convention.
- **Resilience hardening** from an independent architecture review: empty-export guards
  in all 19 bulk modules, config linter + CI validation workflow, export retry/backoff,
  remote-state migration runbook + scaffolding, state-surgery recovery of a poisoned
  local state, and three provider issues identified with draft reports (refresh
  hard-errors on deleted sites; import not hydrating `site_id`/`interface_id`).
- **Docs**: every bulk module README now shows both adoption options (native import
  blocks recommended, catocli alternative) with verified, copy-pasteable examples.

## What's next

- Publish the updated module/CLI releases (release checklist prepared), merge the
  provider branch carrying the remaining bulk-move resources, then restore registry
  pins in the reference repo.
- Move the six folders to versioned remote state and enable the nightly drift-plan
  workflow (runbook ready in `docs/remote-state-migration.md`).
- Provider fixes for the three identified import/refresh gaps will make the site-stack
  adoption plan 100% clean.
