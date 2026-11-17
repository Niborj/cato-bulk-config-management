#!/usr/bin/env python3
"""User/group replication between Cato tenants via catocli + the SCIM connector.

Replicates users and user groups from a SOURCE tenant (normally `<source-profile>`)
into a committed CSV dataset under the shared config_data/ folder
(scim_users.csv / scim_groups.csv), and pushes that dataset into a TARGET
tenant via the custom SCIM connector (`catocli scim import ...`). See
3-scim-users/README.md for the full workflow.

By default export sources from the USER QUERY (`user.userList` /
`user.usersGroupList`), covering EVERY user/group regardless of importType
(MANUAL, SCIM, LDAP, INTEGRATION) -- full coverage. `--source scim` restores
the legacy SCIM-connector-only capture (SCIM-provisioned objects only).
Users/groups the query returns without an external_id (e.g. MANUAL users) get
a synthesized `q:<email>` / `q:<name>` external_id so the import CREATEs them
and group membership joins resolve. NOTE: every pushed user lands as SCIM
importType in the target, even originally-MANUAL ones -- expected for a replica.

Subcommands:
  export --source-profile <p> [--source query|scim] [--import-type manual|scim|ldap|integration|all]
         read-only: query (default, full coverage) or scim-connector -> normalize -> config_data/ CSVs
  push   --target-profile <p>   import the CSVs into the target (users, then groups)
  purge  --target-profile <p>   bulk-delete the target's SCIM groups, then users

Normalization on export (what makes the dataset tenant-portable):
  - user_id / group_id columns are BLANKED: on `scim import`, a missing id
    means CREATE -- exactly what an empty target needs. external_id and
    member_external_ids survive untouched as the cross-tenant join keys.
  - rows are sorted (users by email, groups by display_name) so re-exports
    diff cleanly in git.

Safety:
  - export only ever reads from --source-profile.
  - push/purge REFUSE the source tenant: profiles named <source-profile>* or any
    profile resolving to account <SOURCE_ACCOUNT_ID>. No override flag exists.
  - push/purge default to --dry-run (local schema/row checks, no API
    calls); --execute requires typing the resolved account id, and a TTY.
  - both tenants' profiles must carry scim_url + scim_token
    (catocli configure set --scim-url ... --scim-token ...).

Exit codes: 0 ok, 1 subprocess/data failure, 2 guard refusal.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cato_profile import catocli_env, get_profile, load_protected, warn_if_unprotected  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Repo convention: ALL catocli export data lives under the shared root
# config_data/ folder. The normalized dataset is the committed backup of
# record; raw per-profile captures sit in a gitignored subfolder. Nothing is
# ever written to system temp dirs.
CONFIG_DATA_DIR = os.path.join(REPO_ROOT, "config_data")
USERS_CSV = os.path.join(CONFIG_DATA_DIR, "scim_users.csv")
GROUPS_CSV = os.path.join(CONFIG_DATA_DIR, "scim_groups.csv")
RAW_DIR = os.path.join(CONFIG_DATA_DIR, "scim_raw")  # gitignored captures


def raw_paths(profile_name):
    raw = os.path.join(RAW_DIR, profile_name)
    os.makedirs(raw, exist_ok=True)
    return os.path.join(raw, "users.csv"), os.path.join(raw, "groups.csv")


def export_raw(profile_name):
    """Export a tenant's SCIM users+groups to its repo-local raw dir."""
    users_raw, groups_raw = raw_paths(profile_name)
    for path, kind in ((users_raw, "users"), (groups_raw, "groups")):
        if os.path.exists(path):
            os.remove(path)  # stale capture from a prior run
        run_catocli(profile_name, "scim", "export", kind, "-f", "csv",
                    "--output-file", path)
    return users_raw, groups_raw

# Configurable source-tenant guard -- defaults to EMPTY (nothing protected)
# until the operator declares a source tenant via cbcm.config.json or the
# CBCM_PROTECTED_ACCOUNTS / CBCM_PROTECTED_PROFILES env vars. push/purge then
# hard-refuse that account/profile with NO override flag.
PROTECTED_ACCOUNTS, PROTECTED_PROFILES = load_protected()

# Column contracts, per catocli/templates/scim_{users,groups}.csv
USERS_COLUMNS = ["email", "given_name", "family_name", "external_id", "password", "active", "user_id"]
GROUPS_COLUMNS = ["group_id", "display_name", "external_id", "member_external_ids"]


def log(msg):
    print(msg, file=sys.stderr)


def run_catocli(profile_name, *args):
    result = subprocess.run(
        ["catocli", *args], env=catocli_env(profile_name),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"catocli {' '.join(args)} failed:\n{result.stderr or result.stdout}"
        )
    # catocli can exit 0 with embedded failures (GraphQL "errors" body, or a
    # bulk import summary reporting failed rows). Surface those as errors
    # instead of logging a false "complete".
    out = result.stdout or ""
    try:
        body = json.loads(out)
    except ValueError:
        body = None
    # catocli reports a hard failure as either {"errors": [...]} (GraphQL) or a
    # singular {"error": "...", "success": false} wrapper (e.g. the SCIM import
    # aborting on "Row N: Missing required fields"). Catch BOTH -- missing the
    # singular form let a failed users import pass as success and march on to the
    # groups import. A bare {"success": false, "message": ...} with no error key
    # stays a non-error (empty result), as before.
    if isinstance(body, dict) and (body.get("errors") or body.get("error")):
        raise RuntimeError(f"catocli {' '.join(args)} returned errors:\n{out[-500:]}")
    # Bulk import/purge summaries report per-entity failures as failed_users /
    # failed_groups (NOT a bare "failed"), so sum every failed_* count in the
    # parsed summary. Without this a partial push passes as "complete" and the
    # missing users surface later as rule ruleHasInvalidEntity errors.
    if isinstance(body, dict):
        summary = body.get("summary")
        if isinstance(summary, dict):
            failed = sum(int(v) for k, v in summary.items()
                         if k.startswith("failed") and str(v).strip().isdigit())
            if failed > 0:
                raise RuntimeError(
                    f"catocli {' '.join(args)} reported {failed} failed row(s):\n{out[-500:]}"
                )
    # Fallback for non-JSON / summary-less output: match failed, failed_users,
    # failed_groups, etc. (the original regex missed the _suffix forms).
    failed_rows = re.search(r'"?failed(?:_\w+)?"?\s*[:=]\s*(\d+)', out, re.IGNORECASE)
    if failed_rows and int(failed_rows.group(1)) > 0:
        raise RuntimeError(
            f"catocli {' '.join(args)} reported {failed_rows.group(1)} failed row(s):\n{out[-500:]}"
        )
    return out


def require_scim_creds(profile_name):
    profile = get_profile(profile_name)
    if not (profile.get("scim_url") and profile.get("scim_token")):
        log(f"ERROR: profile {profile_name!r} has no scim_url/scim_token configured.\n"
            f"Run: catocli configure set --profile {profile_name} --scim-url <url> --scim-token <token>")
        sys.exit(2)
    return profile


def guard_target(profile_name):
    """Refuse to mutate the source tenant. No override exists on purpose."""
    warn_if_unprotected(PROTECTED_ACCOUNTS, PROTECTED_PROFILES, action="SCIM push/purge")
    if profile_name in PROTECTED_PROFILES:
        log(f"REFUSED: {profile_name!r} is a protected source-of-truth tenant profile.")
        sys.exit(2)
    profile = require_scim_creds(profile_name)
    if profile["account_id"] in PROTECTED_ACCOUNTS:
        log(f"REFUSED: profile {profile_name!r} resolves to protected account "
            f"{profile['account_id']} (a declared source of truth).")
        sys.exit(2)
    return profile


def confirm_account(account_id, preconfirmed=None):
    # An orchestrator (bulk_apply.py) that already did its own interactive
    # typed-account confirmation can pass that id through to avoid a second
    # prompt. The id must still match the resolved account, and guard_target
    # still hard-blocks the source tenant -- this skips the interactive prompt,
    # not the safety checks.
    if preconfirmed is not None:
        if preconfirmed == account_id:
            log(f"account {account_id} pre-confirmed by orchestrator.")
            return True
        log(f"REFUSED: pre-confirmed {preconfirmed!r} != resolved account {account_id}.")
        return False
    if not sys.stdin.isatty():
        log("REFUSED: --execute requires an interactive terminal.")
        return False
    typed = input(f"Type the target account id ({account_id}) to confirm: ").strip()
    if typed != account_id:
        log(f"REFUSED: typed {typed!r} != resolved account {account_id}.")
        return False
    return True


def read_rows(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def read_export_rows(path):
    """Read a catocli scim export CSV, tolerating the zero-object case.

    When a tenant has no SCIM users/groups, `catocli scim export ... -f csv
    --output-file <path>` exits 0 with "No ... found to export" and writes
    NO file at all -- a missing file here means an empty set, not an error.
    """
    if not os.path.exists(path):
        return [], []
    return read_rows(path)


def write_rows(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def normalize(fieldnames, rows, id_column, sort_key, expected_columns):
    """Blank tenant-local ids, align to the template column set, sort."""
    unknown = [c for c in fieldnames if c not in expected_columns]
    if unknown:
        log(f"note: dropping unexpected export column(s): {', '.join(unknown)}")
    for row in rows:
        row[id_column] = ""
    rows.sort(key=lambda r: (r.get(sort_key) or "").lower())
    return rows


# --- full-coverage source: the user query (all importTypes), not just the ----
# --- SCIM connector. The SCIM `scim export` only returns SCIM-provisioned -----
# --- objects; user.userList / user.usersGroupList return EVERY user/group -----
# --- (MANUAL, SCIM, LDAP, INTEGRATION). We map those into the same SCIM CSV ---
# --- contract and push them with the unchanged `scim import`. --------------
QUERY_PAGE_LIMIT = 1000
IMPORT_TYPE_CHOICES = ["manual", "scim", "ldap", "integration", "all"]


def _import_type_enums(import_type):
    """CLI --import-type -> Cato enum list, or None for 'all' (no filter)."""
    return None if import_type == "all" else [import_type.upper()]


def synth_external_id(key):
    """Deterministic external_id for objects the user query returns without one
    (the userList/usersGroupList selections carry no externalId). Prefixed with
    'q:' so it never collides with a real SCIM external_id, and stable across
    runs so group membership joins + re-imports stay idempotent."""
    return "q:" + (key or "").strip().lower()


def query_json(profile_name, *args):
    out = run_catocli(profile_name, "query", *args)
    try:
        return json.loads(out)
    except ValueError:
        raise RuntimeError(f"catocli query {' '.join(args)} returned non-JSON:\n{out[-500:]}")


def _paged(profile_name, query_args, input_key, filt, dig):
    """Run a paginated user query to exhaustion. `dig(block)` -> (items, total)."""
    collected, frm = [], 0
    while True:
        inp = {"paging": {"from": frm, "limit": QUERY_PAGE_LIMIT}}
        if filt:
            inp["filter"] = filt
        data = query_json(profile_name, *query_args, json.dumps({input_key: inp}))
        items, total = dig(data)
        collected.extend(items)
        frm += len(items)
        if not items or frm >= (total or 0):
            break
    return collected


def query_users(profile_name, import_type):
    """Every user via user.userList -> scim_users.csv rows (external_id synthesized)."""
    types = _import_type_enums(import_type)
    filt = {"userImportType": [{"in": types}]} if types else None
    items = _paged(
        profile_name, ("user", "userList"), "userListInput", filt,
        lambda d: (
            (((d.get("data") or {}).get("user") or {}).get("userList") or {}).get("items") or [],
            ((((d.get("data") or {}).get("user") or {}).get("userList") or {}).get("pageInfo") or {}).get("total"),
        ),
    )
    rows = []
    for u in items:
        email = u.get("email")
        if not email:
            continue
        rows.append({
            "email": email,
            "given_name": u.get("firstName") or "",
            "family_name": u.get("lastName") or "",
            "external_id": synth_external_id(email),
            "password": "",
            "active": "true" if u.get("userStatus") == "ACTIVE" else "false",
            "user_id": "",
        })
    return rows


def query_user_groups(profile_name, import_type):
    """Every user group via usersGroupList (+ per-group members) -> scim_groups.csv rows."""
    types = _import_type_enums(import_type)
    filt = {"importType": [{"in": types}]} if types else None
    groups = _paged(
        profile_name, ("user", "usersGroupList"), "usersGroupListInput", filt,
        lambda d: (
            (((d.get("data") or {}).get("user") or {}).get("usersGroupList") or {}).get("items") or [],
            ((((d.get("data") or {}).get("user") or {}).get("usersGroupList") or {}).get("paging") or {}).get("total"),
        ),
    )
    rows = []
    for g in groups:
        gid, name = g.get("id"), g.get("name") or ""
        emails = []
        frm = 0
        while gid:
            payload = {
                "usersGroupInput": {"group": {"by": "ID", "input": str(gid)}},
                "usersGroupMembersInput": {"paging": {"from": frm, "limit": QUERY_PAGE_LIMIT}},
            }
            data = query_json(profile_name, "user", "usersGroup", "members", json.dumps(payload))
            block = ((((data.get("data") or {}).get("user") or {}).get("usersGroup") or {}).get("members") or {})
            items = block.get("items") or []
            total = ((block.get("paging") or {}).get("total")) or 0
            emails.extend(m.get("email") for m in items if m.get("email"))
            frm += len(items)
            if not items or frm >= total:
                break
        rows.append({
            "group_id": "",
            "display_name": name,
            "external_id": synth_external_id(name),
            "member_external_ids": "|".join(synth_external_id(e) for e in emails),
        })
    return rows


def dedupe_users(users):
    """Drop user rows sharing an external_id (a duplicate display name collapses
    to the same synthesized q:<...> key). The SCIM import is create-by-external_id,
    so a second row with the same key fails; keep the first. Returns (rows, dropped)."""
    seen = set()
    out = []
    dropped = 0
    for u in users:
        ext = u.get("external_id")
        if ext in seen:
            dropped += 1
            continue
        seen.add(ext)
        out.append(u)
    return out, dropped


def dedupe_groups(groups):
    """Collapse groups sharing an external_id (duplicate display names in the
    source collapse to the same synthesized q:<name> key -- e.g. two 'Red Team'
    groups). Merge their member_external_ids as a union so no membership is lost,
    and keep a single row: a duplicate name is ambiguous for name-based policy
    resolution anyway, and the create-by-external_id import rejects the second
    row. Returns (rows, merged)."""
    by_ext = {}
    order = []
    merged = 0
    for g in groups:
        ext = g.get("external_id")
        if ext in by_ext:
            existing = by_ext[ext]
            members = [m for m in (existing.get("member_external_ids") or "").split("|") if m]
            seen = set(members)
            for m in (g.get("member_external_ids") or "").split("|"):
                if m and m not in seen:
                    members.append(m)
                    seen.add(m)
            existing["member_external_ids"] = "|".join(members)
            merged += 1
        else:
            by_ext[ext] = dict(g)
            order.append(ext)
    return [by_ext[e] for e in order], merged


def fill_missing_names(users):
    """SCIM import REQUIRES given_name + family_name; some source users have an
    email but no first/last name (e.g. user@example.com), which
    aborts the whole users import mid-stream ('Row N: Missing required fields').
    Derive a name from the email local part so every row is importable. Returns
    (users, filled_count)."""
    filled = 0
    for u in users:
        g = (u.get("given_name") or "").strip()
        f = (u.get("family_name") or "").strip()
        if g and f:
            continue
        local = (u.get("email") or "").split("@")[0]
        parts = [p for p in local.replace("_", ".").split(".") if p]
        if not g:
            g = parts[0].capitalize() if parts else (local or "User")
        if not f:
            f = " ".join(p.capitalize() for p in parts[1:]) if len(parts) > 1 else g
        u["given_name"], u["family_name"] = g, f
        filled += 1
    return users, filled


def disambiguate_display_names(users):
    """Policies reference users by DISPLAY NAME (given+family), but <source-profile> has
    users that share one (e.g. two 'the operator Anderson's) -- the provider's
    entityLookup then returns >1 match and the rule fails ruleHasInvalidEntity.
    Keep ONE user clean per name (prefer the one whose email local-part matches
    given.family -- the "real" account the policy most likely means) and suffix
    the others' family_name with their email local-part so every display name is
    unique. Returns (users, renamed)."""
    from collections import defaultdict
    byname = defaultdict(list)
    for u in users:
        dn = ((u.get("given_name") or "") + " " + (u.get("family_name") or "")).strip()
        byname[dn].append(u)

    def email_matches_name(u):
        local = (u.get("email") or "").split("@")[0].lower()
        want = ((u.get("given_name") or "") + "." + (u.get("family_name") or "")).lower().replace(" ", ".")
        return local == want

    renamed = 0
    for dn, group in byname.items():
        if len(group) < 2:
            continue
        # primary = first email-matching user, else first by email (stable)
        ordered = sorted(group, key=lambda u: (not email_matches_name(u), (u.get("email") or "").lower()))
        for u in ordered[1:]:
            local = (u.get("email") or "").split("@")[0]
            u["family_name"] = f"{(u.get('family_name') or '').strip()} ({local})".strip()
            renamed += 1
    return users, renamed


def cmd_export(args):
    if args.source == "query":
        get_profile(args.source_profile)  # resolve/validate the profile (uses cato_token, not scim creds)
        log(f"exporting ALL users+groups from {args.source_profile!r} via the user query "
            f"(--import-type {args.import_type}, read-only)")
        users = query_users(args.source_profile, args.import_type)
        groups = query_user_groups(args.source_profile, args.import_type)
        users.sort(key=lambda r: (r.get("email") or "").lower())
        groups.sort(key=lambda r: (r.get("display_name") or "").lower())
    else:  # legacy SCIM-connector source (SCIM-provisioned objects only)
        require_scim_creds(args.source_profile)
        log(f"exporting SCIM-connector users+groups from {args.source_profile!r} (read-only)")
        users_raw, groups_raw = export_raw(args.source_profile)
        u_fields, users = read_export_rows(users_raw)
        g_fields, groups = read_export_rows(groups_raw)
        users = normalize(u_fields, users, "user_id", "email", USERS_COLUMNS)
        groups = normalize(g_fields, groups, "group_id", "display_name", GROUPS_COLUMNS)

    # Collapse rows that collapse to the same synthesized external_id (duplicate
    # display names in the source). The create-by-external_id SCIM import rejects
    # the second occurrence otherwise -- surfaced as the "Red Team" group failing
    # 24/25. Users: keep first; groups: union members.
    users, dropped_u = dedupe_users(users)
    groups, merged_g = dedupe_groups(groups)
    users, filled_n = fill_missing_names(users)
    users, renamed_n = disambiguate_display_names(users)
    if renamed_n:
        log(f"disambiguated {renamed_n} user(s) sharing a display name (suffixed family_name with email local-part)")
    if dropped_u:
        log(f"deduped {dropped_u} duplicate user row(s) by external_id (kept first)")
    if merged_g:
        log(f"merged {merged_g} duplicate group row(s) by external_id (members unioned)")
    if filled_n:
        log(f"filled {filled_n} user(s) missing given_name/family_name from their email")

    # Exported passwords never round-trip; blank column kept for the import contract.
    write_rows(USERS_CSV, USERS_COLUMNS, users)
    write_rows(GROUPS_CSV, GROUPS_COLUMNS, groups)
    log(f"wrote {len(users)} users -> {os.path.relpath(USERS_CSV, REPO_ROOT)}")
    log(f"wrote {len(groups)} groups -> {os.path.relpath(GROUPS_CSV, REPO_ROOT)}")
    log("Done. Review the diff, then push with: "
        "python3 scripts/scim_sync.py push --target-profile <profile>")
    return 0


def check_dataset():
    """Local-only validation of scim_data/ against the import contracts."""
    problems = []
    for path, columns, id_col, key in (
        (USERS_CSV, USERS_COLUMNS, "user_id", "email"),
        (GROUPS_CSV, GROUPS_COLUMNS, "group_id", "display_name"),
    ):
        if not os.path.exists(path):
            problems.append(f"missing {os.path.relpath(path, REPO_ROOT)} (run export first)")
            continue
        fields, rows = read_rows(path)
        if fields != columns:
            problems.append(f"{os.path.basename(path)}: columns {fields} != expected {columns}")
            continue
        for i, row in enumerate(rows, start=2):
            if not row.get(key):
                problems.append(f"{os.path.basename(path)} line {i}: empty {key}")
            if not row.get("external_id"):
                problems.append(f"{os.path.basename(path)} line {i}: empty external_id (cross-tenant join key)")
            if row.get(id_col):
                problems.append(f"{os.path.basename(path)} line {i}: {id_col} is set -- dataset "
                                f"should be id-free (would UPDATE instead of CREATE on the target)")
    # every group member must exist as a user external_id
    if os.path.exists(USERS_CSV) and os.path.exists(GROUPS_CSV):
        _, users = read_rows(USERS_CSV)
        known = {u["external_id"] for u in users if u.get("external_id")}
        _, groups = read_rows(GROUPS_CSV)
        for g in groups:
            for member in filter(None, (g.get("member_external_ids") or "").split("|")):
                if member not in known:
                    problems.append(f"group {g.get('display_name')!r}: member {member!r} "
                                    f"not present in scim_users.csv")
    return problems


def reconcile_users(profile_name, users, attempts=4):
    """The bulk SCIM import occasionally reports success for users that don't
    actually persist (observed ~2-3%, scattered, no batch pattern; re-importing
    them individually succeeds). Re-query the target and re-import any missing
    users until they all exist, so group-member resolution and policy user refs
    don't fail on a phantom "created" count. Only MISSING users are re-imported.
    Raises if any remain after `attempts` rounds."""
    recon = os.path.join(os.path.dirname(USERS_CSV), "scim_users_reconcile.csv")

    def present_external_ids():
        # Use the live get_users API, NOT `scim export`: the export endpoint is
        # eventually consistent and can return EMPTY for minutes right after a
        # bulk import (observed post purge+reimport), which made all 455 users
        # look absent and sent the reconcile into a full individual re-import.
        out = run_catocli(profile_name, "scim", "get_users")
        body = json.loads(out)
        users_live = (body.get("data") or {}).get("users") or body.get("users") or []
        return {u.get("externalId") for u in users_live if u.get("externalId")}

    for attempt in range(1, attempts + 1):
        present = present_external_ids()
        missing = [u for u in users if u.get("external_id") not in present]
        if not missing:
            log(f"reconcile users: all {len(users)} present in target.")
            return
        log(f"reconcile users (attempt {attempt}/{attempts}): {len(missing)} user(s) "
            f"reported created but absent; re-importing INDIVIDUALLY "
            f"({', '.join(u['external_id'] for u in missing[:5])}"
            f"{'...' if len(missing) > 5 else ''}) ...")
        # The bulk import drops a deterministic subset when they are imported
        # together (catocli batch bug), but each imports fine on its own -- so
        # re-import one user per call.
        for u in missing:
            write_rows(recon, USERS_COLUMNS, [u])
            try:
                run_catocli(profile_name, "scim", "import", "users", recon, "-f", "csv")
            except RuntimeError as exc:
                log(f"  reconcile import error for {u.get('external_id')}: {exc}")
        if os.path.exists(recon):
            os.remove(recon)

    still = [u["external_id"] for u in users if u.get("external_id") not in present_external_ids()]
    if still:
        raise RuntimeError(
            f"{len(still)} user(s) still absent after {attempts} reconcile rounds: "
            + ", ".join(still[:10]))


def cmd_push(args):
    profile = guard_target(args.target_profile)
    problems = check_dataset()
    if problems:
        log("dataset problems:\n  " + "\n  ".join(problems))
        return 1
    _, users = read_rows(USERS_CSV)
    _, groups = read_rows(GROUPS_CSV)
    log(f"dataset OK: {len(users)} users, {len(groups)} groups "
        f"-> target {args.target_profile!r} (account {profile['account_id']})")
    if not args.execute:
        log("DRY-RUN (default): no API calls made. Re-run with --execute to push.")
        return 0
    if not confirm_account(profile["account_id"], getattr(args, "confirmed_account", None)):
        return 2
    # The SCIM connector does NOT persist a user created inactive -- the create
    # silently no-ops -- and an inactive user can't be a resolvable group member
    # or policy ref anyway. Import every user active=true so they all land; the
    # committed dataset keeps the real active value for fidelity.
    push_users = [{**u, "active": "true"} for u in users]
    push_csv = os.path.join(os.path.dirname(USERS_CSV), "scim_users_push.csv")
    write_rows(push_csv, USERS_COLUMNS, push_users)
    n_inactive = sum(1 for u in users if (u.get("active") or "").lower() != "true")
    log(f"importing users (create mode, active=true so {n_inactive} inactive user(s) persist) ...")
    try:
        print(run_catocli(args.target_profile, "scim", "import", "users", push_csv,
                          "-f", "csv", "--write-processed-file"))
    finally:
        if os.path.exists(push_csv):
            os.remove(push_csv)
    # Verify every user actually persisted before importing groups -- a phantom
    # "created" count leaves group members (and later policy user refs) dangling.
    reconcile_users(args.target_profile, push_users)
    log("importing groups ...")
    print(run_catocli(args.target_profile, "scim", "import", "groups", GROUPS_CSV,
                      "-f", "csv", "--write-processed-file"))
    log("push complete. Verify with: catocli scim get_users / get_groups under the target profile.")
    return 0


def cmd_purge(args):
    profile = guard_target(args.target_profile)
    log(f"purging SCIM groups+users on {args.target_profile!r} (account {profile['account_id']})")
    users_raw, groups_raw = export_raw(args.target_profile)
    _, users = read_export_rows(users_raw)
    _, groups = read_export_rows(groups_raw)
    log(f"target currently has {len(users)} users, {len(groups)} groups")
    if not users and not groups:
        log("nothing to purge.")
        return 0
    if not args.execute:
        log("DRY-RUN (default): nothing deleted.")
        return 0
    if not confirm_account(profile["account_id"], getattr(args, "confirmed_account", None)):
        return 2
    # groups first (membership edges), then users. Each phase is attempted
    # INDEPENDENTLY: a groups-purge error must NOT skip the users purge.
    # Every object is DISABLED first, then DELETED (individually -- the bulk
    # `catocli scim purge` delete_phase is broken, and per-object calls give
    # precise failure attribution). A disable failure is logged but the delete
    # is still attempted; only a failed DELETE counts as a failure.
    errors = []

    def disable_then_delete(kind, id_key, obj_id):
        try:
            run_catocli(args.target_profile, "scim", f"disable_{kind}",
                        "-p", json.dumps({id_key: obj_id}))
        except RuntimeError as exc:
            log(f"  disable_{kind} {obj_id} error (continuing to delete): {exc}")
        run_catocli(args.target_profile, "scim", f"delete_{kind}",
                    "-p", json.dumps({id_key: obj_id}))

    if groups:
        log(f"purging {len(groups)} group(s) (disable + delete, individually) ...")
        n_failed = 0
        for g in groups:
            gid = g.get("group_id")
            if not gid:
                continue
            try:
                disable_then_delete("group", "group_id", gid)
            except RuntimeError as exc:
                log(f"  delete_group {gid} error: {exc}")
                n_failed += 1
        if n_failed:
            errors.append(f"groups ({n_failed} failed)")
    if users:
        log(f"purging {len(users)} user(s) (disable + delete, individually) ...")
        n_failed = 0
        for i, u in enumerate(users, start=1):
            uid = u.get("user_id")
            if not uid:
                continue
            try:
                disable_then_delete("user", "user_id", uid)
            except RuntimeError as exc:
                log(f"  delete_user {uid} error: {exc}")
                n_failed += 1
            if i % 50 == 0:
                log(f"  ... {i}/{len(users)} users processed")
        if n_failed:
            errors.append(f"users ({n_failed} failed)")
    if errors:
        log(f"purge completed WITH ERRORS in: {', '.join(errors)} "
            "(re-run to retry; SCIM purge is idempotent on already-deleted objects).")
        return 1
    log(f"purge complete: cleared {len(groups)} group(s) and {len(users)} user(s).")
    return 0


def main():
    global CONFIG_DATA_DIR, USERS_CSV, GROUPS_CSV, RAW_DIR
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # --config-dir is shared by every subcommand (must come AFTER the
    # subcommand name, e.g. `scim_sync.py push --config-dir DIR`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-dir", default=CONFIG_DATA_DIR,
                        help="directory holding/receiving the SCIM CSV dataset "
                             "(default: ./config_data)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", parents=[common],
                              help="export source tenant users/groups into config_data/ SCIM CSVs")
    p_export.add_argument("--source-profile", required=True)
    p_export.add_argument("--source", choices=["query", "scim"], default="query",
                          help="query (default): ALL users/groups via the user query (full coverage, "
                               "any importType); scim: only SCIM-connector-provisioned objects (legacy).")
    p_export.add_argument("--import-type", choices=IMPORT_TYPE_CHOICES, default="all",
                          help="query source only: filter by importType. Default all -- keep all so "
                               "group members (which may differ in importType from the group) resolve.")
    p_export.set_defaults(func=cmd_export)

    p_push = sub.add_parser("push", parents=[common], help="import scim_data/ into the target tenant")
    p_push.add_argument("--target-profile", required=True)
    p_push.add_argument("--execute", action="store_true",
                        help="actually push (default is a local dry-run with no API calls)")
    p_push.add_argument("--confirmed-account",
                        help="account id already confirmed by an orchestrator (bulk_apply.py); "
                             "skips the interactive typed-account prompt when it matches the "
                             "resolved account. The source-tenant block still applies.")
    p_push.set_defaults(func=cmd_push)

    p_purge = sub.add_parser("purge", parents=[common], help="bulk-delete the target tenant's SCIM groups+users")
    p_purge.add_argument("--target-profile", required=True)
    p_purge.add_argument("--execute", action="store_true",
                         help="actually delete (default is a read-only inventory dry-run)")
    p_purge.add_argument("--confirmed-account",
                         help="account id already confirmed by an orchestrator (purge_all.py); "
                              "skips the interactive typed-account prompt when it matches the "
                              "resolved account. The source-tenant block still applies.")
    p_purge.set_defaults(func=cmd_purge)

    args = parser.parse_args()
    # Redirect the dataset paths at the chosen config dir (default ./config_data).
    CONFIG_DATA_DIR = os.path.abspath(args.config_dir)
    USERS_CSV = os.path.join(CONFIG_DATA_DIR, "scim_users.csv")
    GROUPS_CSV = os.path.join(CONFIG_DATA_DIR, "scim_groups.csv")
    RAW_DIR = os.path.join(CONFIG_DATA_DIR, "scim_raw")
    try:
        return args.func(args)
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
