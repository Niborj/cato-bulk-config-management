# Remote state migration (prerequisite for drift CI and multi-operator safety)

Status: **scaffolded, not activated.** Every folder still uses local state. This doc is
the runbook for moving all six folders to versioned, locked remote state — the
prerequisite for scheduled drift detection, CI plans, and any second operator/machine.
Each folder ships a commented `backend.tf.example`; activation is the operator's call.

## Why (evidence from this repo)

- 0-sites local state silently tracked four sites deleted from the tenant (224579–82);
  provider 1.5.x hard-errors refreshing them, making the folder unplannable until manual
  `terraform state rm` surgery (2026-11-04; backup kept as
  `0-sites/terraform.tfstate.pre-surgery-*.bak`). Versioned remote state turns that class
  of incident into a revert; scheduled plans turn it into a same-day notification.
- Local state on a shared path is unencrypted at rest and invisible to CI.

## Option A — S3 backend (recommended if AWS is the natural home)

One bucket, one key per folder, versioning + SSE on, native lockfile locking
(Terraform >= 1.10; use a DynamoDB lock table instead on older versions).

```hcl
terraform {
  backend "s3" {
    bucket       = "<state-bucket>"          # create once: versioning + SSE-KMS on
    key          = "<target-profile>/0-sites.tfstate"   # per-folder key
    region       = "us-east-1"
    use_lockfile = true                            # TF >= 1.10 native S3 locking
  }
}
```

## Option B — HCP Terraform (Terraform Cloud)

One workspace per folder (`<target-profile>-0-sites`, …), VCS-driven or CLI-driven; gives
state versioning, locking, encrypted storage, and plan runs in one move.

```hcl
terraform {
  cloud {
    organization = "<org>"
    workspaces { name = "<target-profile>-0-sites" }
  }
}
```

## Migration procedure (per folder, after creating the bucket/workspaces)

```bash
cd 0-sites
cp terraform.tfvars.example terraform.tfvars   # if not already present (gitignored)
mv backend.tf.example backend.tf                # uncomment/fill in the backend block
terraform init -migrate-state                   # answer "yes" to copy local -> remote
terraform plan                                  # must match the pre-migration plan
mv terraform.tfstate terraform.tfstate.migrated-$(date +%Y%m%d).bak  # keep, gitignored
```

Repeat for `1-dependencies`, `3-policies/internet-firewall`, `3-policies/wan-firewall`,
`3-policies/wan-network`, `3-policies/tls-inspection`.

## 3-policies/* state credibility audit (do BEFORE migrating those folders)

The four policy folders' local states predate the bulk brownfield work and are not
credible baselines (e.g. `3-policies/internet-firewall/terraform.tfstate` is at serial 245 but
holds ~1 rule/1 section vs 36 rules in `config_data/`). Per folder:

```bash
terraform state list            # what does state THINK it manages?
terraform plan                  # vs what the JSON says
```

- If state holds a stale subset: `terraform state rm` the stale addresses (back up the
  state file first), then adopt cleanly via the folder README's Option A import blocks.
- Only migrate a state to the backend after its plan is clean (imports-only or
  no-changes) — don't enshrine residue.

## Nightly drift detection (enable AFTER backend migration)

Add as `.github/workflows/drift-plan.yml`; needs `TF_VAR_cato_token_<source-profile>_dev`,
`TF_VAR_account_id_<source-profile>_dev`, and backend credentials as repo secrets.

```yaml
name: nightly-drift-plan
on:
  schedule: [{ cron: "0 9 * * *" }]   # 09:00 UTC daily
  workflow_dispatch: {}
jobs:
  plan:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        folder: [0-sites, 1-dependencies, 3-policies/internet-firewall, 3-policies/wan-firewall, 3-policies/wan-network, 3-policies/tls-inspection]
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
      - name: plan
        working-directory: ${{ matrix.folder }}
        env:
          TF_VAR_cato_token: ${{ secrets.CATO_TOKEN }}
          TF_VAR_account_id: ${{ secrets.CATO_ACCOUNT_ID }}
        run: |
          terraform init -input=false
          terraform plan -input=false -detailed-exitcode -no-color > plan.txt; code=$?
          if [ "$code" = "2" ]; then
            echo "DRIFT DETECTED in ${{ matrix.folder }}"; tail -50 plan.txt
            exit 2   # surfaces as a failed job -> notification; wire Slack here if desired
          fi
```

Never auto-apply from this workflow — drift output is a review signal (the live tenant
may be the correct side; re-export instead of overwriting, per repo convention).
