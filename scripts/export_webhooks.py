#!/usr/bin/env python3
"""Interim richer webhook export -> config_data/webhooks.json.

WHY THIS EXISTS (interim workaround)
------------------------------------
`catocli export webhooks` already works, but its query is intentionally minimal
(id, name, enabled, url, requestMethod, relatedWebhookId) -- it never selects
`auth`, partly to stay credential-free and partly because the full generated
webhook query selects the `auth` union member `CustomHeadersAuth`, a type the
bundled schema advertises but the live `notification` service has not deployed
yet, so it fails at query time with:
    Validation error (UnknownType@[...auth]) : Unknown type 'CustomHeadersAuth'

This script sidesteps that by issuing a RAW query (via `catocli raw`) that
selects only `auth { authType }` -- the scalar on the interface, NOT any
`... on <AuthType>` union-member fragment -- so it never references the
undeployed type. It additionally captures `body`, `header`, and
`responseMapping`, giving a fuller webhook picture than the stock export:

    id, name, enabled, url, requestMethod,
    auth { authType },            # auth KIND only -- never secrets
    header [{ name, value }],
    body,                         # payload template ($variables), NOT a secret
    responseMapping [{ key, value }],
    relatedWebhookId

CREDENTIAL SAFETY: the query selects only `authType` (NO_AUTH / BASIC /
BEARER / CUSTOM_HEADER ...), never the secret auth subfields (password /
bearerToken / value). `body` is a payload template, not a credential. So the
output carries no live webhook secrets -- those still come from the
`webhook_secrets` tfvars when 1-dependencies' cato_webhook resource creates
them.

RETIREMENT: fold these richer fields into `catocli export webhooks` (and drop
the raw hack) once the notification service deploys `CustomHeadersAuth` and the
generated webhook query stops erroring. Tracked alongside the
schemaTypeExclusions discussion.

Usage:
    python3 scripts/export_webhooks.py --profile <profile>
    python3 scripts/export_webhooks.py --profile <source-profile> --output config_data/webhooks.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cato_profile  # noqa: E402

DEFAULT_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config_data",
)
DEFAULT_OUTPUT = os.path.join(DEFAULT_CONFIG_DIR, "webhooks.json")

# Only `auth { authType }` is selected -- the scalar on AuthInterface, never a
# `... on CustomHeadersAuth` (or any other member) fragment, so this never
# references the undeployed type. header/body/responseMapping round out the
# structural picture; no secret auth subfields are requested.
WEBHOOK_LIST_QUERY = (
    "query notificationWebhookList ( $accountId:ID! ) "
    "{ notification ( accountId:$accountId ) { integration { webhookList "
    "{ items { id name enabled url requestMethod auth { authType } "
    "header { name value } body responseMapping { key value } relatedWebhookId } "
    "paging { total } } } } }"
)

# Field order preserved on output for stable diffs.
ITEM_FIELDS = [
    "id", "name", "enabled", "url", "requestMethod",
    "auth", "header", "body", "responseMapping", "relatedWebhookId",
]


def shape_item(raw):
    """Reshape one API webhook item into the canonical config_data shape."""
    auth = raw.get("auth") or {}
    item = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "enabled": raw.get("enabled"),
        "url": raw.get("url"),
        "requestMethod": raw.get("requestMethod"),
        # auth KIND only -- no secrets are ever present in the response.
        "auth": {"authType": auth.get("authType")} if auth else None,
        "header": raw.get("header") or [],
        "body": raw.get("body"),
        "responseMapping": raw.get("responseMapping") or [],
        "relatedWebhookId": raw.get("relatedWebhookId"),
    }
    return {k: item[k] for k in ITEM_FIELDS}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True,
                        help="catocli profile name (from ~/.cato/credentials) to export from.")
    parser.add_argument("--account-id",
                        help="Override the account id (defaults to the profile's account_id).")
    parser.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                        help="config_data directory to write webhooks.json into "
                             "(default: ./config_data). Ignored if --output is given.")
    parser.add_argument("--output", default=None,
                        help="Output path (default: <config-dir>/webhooks.json).")
    args = parser.parse_args()
    if args.output is None:
        args.output = os.path.join(args.config_dir, "webhooks.json")

    try:
        profile = cato_profile.get_profile(args.profile)
    except cato_profile.ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    account_id = args.account_id or profile["account_id"]

    payload = json.dumps({
        "query": WEBHOOK_LIST_QUERY,
        "variables": {"accountId": str(account_id)},
        "operationName": "notificationWebhookList",
    })

    try:
        stdout = cato_profile.run_catocli(args.profile, "raw", payload)
    except cato_profile.ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        print(f"ERROR: could not parse catocli response:\n{stdout}", file=sys.stderr)
        return 1

    if response.get("errors"):
        print("ERROR: GraphQL errors in webhook export:", file=sys.stderr)
        print(json.dumps(response["errors"], indent=2), file=sys.stderr)
        return 1

    try:
        items = (response["data"]["notification"]["integration"]
                 ["webhookList"]["items"]) or []
    except (KeyError, TypeError):
        print(f"ERROR: unexpected response shape:\n{stdout}", file=sys.stderr)
        return 1

    shaped = sorted((shape_item(it) for it in items),
                    key=lambda w: (w.get("name") or "").lower())

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(shaped, fh, indent=4)
        fh.write("\n")

    print(f"Wrote {len(shaped)} webhook(s) (account {account_id}) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
