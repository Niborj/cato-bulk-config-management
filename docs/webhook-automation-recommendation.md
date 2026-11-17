# Recommendation: automatic backups via Cato webhook → GitHub Action

**Status: GitHub side built, Cato side still manual.** The workflow, export script, and
`config_data_archive/` landing directory described below now exist in this repo (see
"GitHub-side configuration" below for what each piece does). What's still missing is the
Cato-side half: the actual webhook integration + alert rule that calls this repo's
`repository_dispatch` endpoint on a policy save, plus the `CATO_TOKEN`/`CATO_ACCOUNT_ID`
repo secrets — both need to be done by a human with Cato tenant admin and GitHub repo
admin access respectively; nothing in this repo can create either on its own.

## What's actually true about Cato's webhook mechanism (verified, not assumed)

Checked against the live account via the Cato MCP tools and the Go SDK (`cato-go-sdk/models/models.go`) rather than guessed:

- This account already has active webhook integrations (`notification.integration.webhook`): AWS EventBridge, two ServiceNow integrations, and a Slack SOC IR integration. This is a proven, already-used mechanism on this tenant — not a theoretical feature.
- A Cato webhook integration (`CreateWebhookInput` / `Webhook` in the SDK) is a configurable **outbound HTTP call**: `RequestMethod`, `URL`, a list of custom `Header`s, a templated `Body`, and an `Auth` block supporting **Basic, Bearer, or custom-header** authentication.
- The Bearer-auth option is the key fact: it means Cato can call an authenticated third-party API — including GitHub's — **directly**, with no relay service in between.

What I could *not* verify locally (would need to confirm in the Cato UI/support docs): the exact audit/alert event category that fires "on policy saved," and the exact set of body-template merge-field tokens available for that event. `catocli query notification integration webhookFields` (confirmed to exist as a CLI operation) is the way to enumerate available template tokens once a webhook integration is created against a specific event type — do that as the first implementation step before writing the body template.

## Recommended architecture: direct Cato → GitHub, no relay

```
Cato policy saved
   → Alert/Audit rule (configured in Cato UI, fires on config-change event)
   → Subscription Group → Webhook Integration
   → POST https://api.github.com/repos/<org>/<repo>/dispatches
        Auth: Bearer <fine-grained GitHub PAT>
        Header: Accept: application/vnd.github+json
        Body: {"event_type": "cato-policy-saved", "client_payload": {...}}
   → GitHub Actions workflow triggered by `repository_dispatch`
   → runs `catocli export` for the affected policy type(s)
   → commits refreshed JSON / opens a PR if it differs from what's committed
```

### Why no relay

GitHub's `repository_dispatch` API accepts exactly the shape Cato's webhook can produce natively (POST, Bearer auth, custom header, JSON body) — there's no protocol mismatch forcing a translation layer. Adding a Lambda/Cloud Function in between would just be another thing to deploy, monitor, and secure for no functional gain. Keep it as a documented fallback (below), not the default.

### Cato-side configuration (future work)

1. Create a GitHub **fine-grained personal access token**, repo-scoped to this repository only, with `Contents: write` and `Actions: write` (or `Contents: read` + `Actions: write` if you only need to trigger the workflow and let the workflow itself commit via its own `GITHUB_TOKEN`). Fine-grained over classic — minimize blast radius if it ever leaks.
2. In the Cato Management Application, create a webhook integration (`catocli` can also do this via a `mutation notification createWebhook` call, once the exact input shape is confirmed with `catocli catocli_help "mutation notification"` against your CLI version):
   - URL: `https://api.github.com/repos/<org>/<repo>/dispatches`
   - Method: `POST`
   - Header: `Accept: application/vnd.github+json`
   - Auth: Bearer, token = the PAT from step 1
   - Body: `{"event_type": "cato-policy-saved", "client_payload": {"policy_type": "{{...}}"}}` — fill in the actual merge-field token(s) once discovered via `webhookFields`/the UI's alert-rule editor.
3. Create (or extend an existing) Subscription Group containing this webhook integration.
4. Create an Alert/Audit rule that fires on configuration-change events for the policy types this repo manages, delivering to that Subscription Group. This is the piece that needs confirming in-app — Cato's audit/alert event taxonomy isn't in the CLI/SDK sources available locally.
5. Test using the webhook integration's test action (`TestWebhookPayload` exists in the SDK — there's a "send test" capability) before relying on it.

### GitHub-side configuration (built)

[`.github/workflows/cato-export.yml`](../.github/workflows/cato-export.yml):

- Trigger: `on: repository_dispatch: types: [cato-policy-saved]`, plus `workflow_dispatch` for manual/test runs.
- Steps: install `catocli`, `catocli configure set` using the `CATO_TOKEN`/`CATO_ACCOUNT_ID` repo secrets (not yet added — see below), run `catocli export all --no-account-suffix --archive-directory config_data_archive` — which natively covers all 28 types this repo manages in-process (requires the catocli release that ships `export all`, not yet on PyPI as of 2026-11-06) — into [`config_data_archive/`](../config_data_archive/), then open/update a PR against `main` via `peter-evans/create-pull-request` when the export differs from what's committed. Chose the PR path over committing straight to a branch with no review step, so a human confirms an intentional live change before it becomes the new baseline.
- Deliberately **does not** run `terraform apply`. This repo's folders currently use **local** state (an explicit, agreed-upon choice for now) — local `.tfstate` can't be read/written reliably by a GitHub Actions runner across invocations. If this automation is ever extended to also apply, migrating each folder's backend to something CI can share (Terraform Cloud, S3+DynamoDB, etc.) needs to happen first.
- Writes to `config_data_archive/`, not `config_data/` — see `config_data_archive/README.md` for why (protects hand-curated fixes already in `config_data/` from a blind automated overwrite).

### Still needed (manual, outside this repo)

1. Add the `CATO_TOKEN` and `CATO_ACCOUNT_ID` repo secrets (Settings → Secrets and variables → Actions) — needs GitHub repo admin access.
2. Everything in "Cato-side configuration (future work)" below — needs Cato tenant admin access.

### Fallback: relay service

Only reach for this if, once implemented, Cato's body templating turns out not to be flexible enough to produce GitHub's exact `{event_type, client_payload}` JSON shape (e.g., if template tokens can't be nested or the body can't be a single flat JSON object). In that case, insert a minimal serverless function (AWS Lambda / Azure Function / Cloud Function) as the webhook target instead of GitHub directly: Cato POSTs whatever shape it can produce, the function reshapes it and forwards the `repository_dispatch` call to GitHub with the PAT. Extra moving part, extra thing to secure and monitor — avoid unless the direct path proves infeasible.

### Security notes

- The GitHub PAT lives only in Cato's webhook auth configuration (stored server-side by Cato) — it is never committed to this repo.
- Scope the PAT to this one repository and the minimum permissions needed (`Actions: write`, and `Contents: write` only if the workflow itself won't use its own `GITHUB_TOKEN` to commit).
- Rotate the PAT on a schedule consistent with your org's token-rotation policy; fine-grained PATs can also be given an expiration date at creation time.
