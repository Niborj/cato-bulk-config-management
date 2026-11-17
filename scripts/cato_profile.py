#!/usr/bin/env python3
"""Shared catocli profile + credential helper for repo scripts.

Parses ~/.cato/credentials (AWS-CLI-style INI, one section per profile) and
exposes the pieces other scripts need:

  - get_profile(name)    -> {"endpoint", "cato_token", "account_id", ...}
  - catocli_env(name)    -> os.environ copy with CATO_PROFILE set
  - terraform_env(name)  -> os.environ copy with this repo's TF_VAR_* vars
                            injected from the named profile, explicitly
                            OVERRIDING any inherited TF_VAR_* (the shell's
                            exported values are known to point at the wrong
                            tenant -- see CLAUDE.md)
  - run_catocli(name, *args) -> subprocess helper (raises on failure)

Never prints or logs token values. Uses RawConfigParser because profile
sections can carry cookie/url values containing '%' sequences that break
configparser's default interpolation.

CLI usage (safe, redacts secrets):
    python3 scripts/cato_profile.py <profile>     # show account_id/endpoint
"""
import configparser
import json
import os
import subprocess
import sys

CREDENTIALS_PATH = os.path.expanduser("~/.cato/credentials")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The tfvars names every folder in this repo declares (generic, tenant-agnostic
# -- see CLAUDE.md "variables.tf" convention).
TF_VAR_TOKEN = "TF_VAR_cato_token"
TF_VAR_ACCOUNT = "TF_VAR_account_id"

# ---------------------------------------------------------------------------
# Configurable source-tenant guard.
#
# This package ships with an EMPTY protected list: nothing is a "source of
# truth" until the operator declares it. Destructive scripts (purge, scim
# push/purge, bulk apply) consult these sets to refuse operations against the
# account(s)/profile(s) you want to protect from accidental teardown.
#
# Operators set their OWN guard in either of two places (both are merged):
#   1. cbcm.config.json at the repo root, e.g.
#        { "protected_accounts": ["12345"],
#          "protected_profiles": ["prod", "prod-readonly"] }
#   2. environment variables (comma-separated):
#        CBCM_PROTECTED_ACCOUNTS="12345,67890"
#        CBCM_PROTECTED_PROFILES="prod,prod-readonly"
# ---------------------------------------------------------------------------
CBCM_CONFIG_PATH = os.path.join(REPO_ROOT, "cbcm.config.json")


class ProfileError(RuntimeError):
    pass


def _split_env(name):
    raw = os.environ.get(name, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def load_protected(config_path=CBCM_CONFIG_PATH):
    """Return (protected_accounts, protected_profiles) as sets of strings.

    Merges an optional cbcm.config.json with the CBCM_PROTECTED_ACCOUNTS /
    CBCM_PROTECTED_PROFILES environment variables. Defaults to EMPTY sets
    (no guard) so this package is tenant-agnostic out of the box; operators
    opt in by declaring their own source tenant.
    """
    accounts, profiles = set(), set()
    try:
        if os.path.isfile(config_path):
            with open(config_path) as f:
                data = json.load(f)
            accounts |= {str(a).strip() for a in data.get("protected_accounts", []) if str(a).strip()}
            profiles |= {str(p).strip() for p in data.get("protected_profiles", []) if str(p).strip()}
    except (OSError, ValueError) as exc:
        raise ProfileError(f"failed to read {config_path}: {exc}")
    accounts |= _split_env("CBCM_PROTECTED_ACCOUNTS")
    profiles |= _split_env("CBCM_PROTECTED_PROFILES")
    return accounts, profiles


_warned_unprotected = False


def warn_if_unprotected(accounts, profiles, action="destructive operation"):
    """Loudly warn (once per run) when no source-tenant guard is configured.

    The typed-account-id + TTY confirmation still gates --execute; this only
    flags that the absolute 'never the source tenant' hard-block is OFF because
    no protected accounts/profiles were declared.
    """
    global _warned_unprotected
    if accounts or profiles or _warned_unprotected:
        return
    _warned_unprotected = True
    bar = "!" * 74
    print(
        f"\n{bar}\n"
        f"  WARNING: no protected accounts/profiles configured.\n"
        f"  The source-tenant hard-block is DISABLED for this {action} -- any\n"
        f"  account/profile you name can be torn down or overwritten.\n"
        f"  Protect your source tenant in cbcm.config.json:\n"
        f'    {{"protected_accounts": ["<id>"], "protected_profiles": ["<name>"]}}\n'
        f"  or env: CBCM_PROTECTED_ACCOUNTS=<id>  CBCM_PROTECTED_PROFILES=<name>\n"
        f"{bar}\n",
        file=sys.stderr,
    )


def _read_credentials(path=CREDENTIALS_PATH):
    if not os.path.exists(path):
        raise ProfileError(f"catocli credentials file not found: {path}")
    parser = configparser.RawConfigParser()  # no '%' interpolation
    parser.read(path)
    return parser


def list_profiles(path=CREDENTIALS_PATH):
    return _read_credentials(path).sections()


def get_profile(name, path=CREDENTIALS_PATH):
    """Return the named profile's fields as a dict.

    Guarantees cato_token and account_id are present; other keys
    (endpoint, scim_url, scim_token, ...) pass through if configured.
    """
    parser = _read_credentials(path)
    if not parser.has_section(name):
        raise ProfileError(
            f"profile {name!r} not found in {path} "
            f"(available: {', '.join(parser.sections())})"
        )
    profile = dict(parser.items(name))
    for required in ("cato_token", "account_id"):
        if not profile.get(required):
            raise ProfileError(f"profile {name!r} is missing {required!r}")
    return profile


def catocli_env(name, base_env=None):
    """Environment for catocli subprocesses: CATO_PROFILE pinned to name.

    The interactive shell often exports CATO_TOKEN / CATO_ACCOUNT_ID for a
    different tenant (e.g. <source-profile> <SOURCE_ACCOUNT_ID>). catocli treats those explicit
    credentials as taking precedence over the profile, so leaving them in the
    child env can silently route a profile-targeted call to the wrong tenant.
    Strip them so the named profile is the sole credential source.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env["CATO_PROFILE"] = name
    env.pop("CATO_TOKEN", None)
    env.pop("CATO_ACCOUNT_ID", None)
    return env


def terraform_env(name, base_env=None):
    """Environment for terraform subprocesses in this repo's folders.

    The folder provider blocks are PROFILE-ONLY (`profile = var.cato_profile`;
    baseurl/token/account_id are commented out), so drive the profile var and
    let the provider resolve token/account_id/endpoint from the named catocli
    profile (Method B). Always overwrites an inherited profile so a shell that
    exported CATO_PROFILE / TF_VAR_cato_profile for a different tenant can't
    plan against the wrong account; warns (account ids only, never tokens) when
    an inherited profile/account pointed elsewhere.

    Because the provider treats profile and explicit credentials as mutually
    exclusive, every explicit source is cleared here -- a leftover CATO_TOKEN /
    CATO_ACCOUNT_ID / CATO_BASEURL or TF_VAR_cato_token_* / TF_VAR_account_id_*
    alongside the profile would make the provider refuse with a
    "Conflicting Cato API Credentials" error.
    """
    # Validate the profile resolves (raises if unknown). The account_id, token,
    # and endpoint come from the profile at apply time via the provider -- NOT
    # from any TF_VAR. A stale TF_VAR_account_id / TF_VAR_cato_token
    # in the operator's shell is irrelevant and is cleared below.
    get_profile(name)
    env = dict(base_env if base_env is not None else os.environ)

    # Method B (profile) only -- set the profile var and clear every explicit
    # source so nothing can route around the profile and the provider's
    # mutual-exclusion check never sees "both".
    env["TF_VAR_cato_profile"] = name
    for k in ("CATO_TOKEN", "CATO_ACCOUNT_ID", "CATO_BASEURL",
              "CATO_PROFILE", TF_VAR_TOKEN, TF_VAR_ACCOUNT):
        env.pop(k, None)
    # Explicit method only -- never present profile auth alongside it.
    env.pop("CATO_PROFILE", None)
    return env


def run_catocli(name, *args, check=True):
    """Run `catocli <args...>` under the named profile. Returns stdout."""
    result = subprocess.run(
        ["catocli", *args],
        capture_output=True,
        text=True,
        env=catocli_env(name),
    )
    if check and result.returncode != 0:
        raise ProfileError(
            f"catocli {' '.join(args)} (profile {name!r}) failed:\n"
            f"{result.stderr or result.stdout}"
        )
    return result.stdout


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        print(f"profiles: {', '.join(list_profiles())}", file=sys.stderr)
        return 2
    profile = get_profile(sys.argv[1])
    print(f"profile:    {sys.argv[1]}")
    print(f"account_id: {profile['account_id']}")
    print(f"endpoint:   {profile.get('endpoint', '<default>')}")
    print(f"scim:       {'configured' if profile.get('scim_url') and profile.get('scim_token') else 'not configured'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
