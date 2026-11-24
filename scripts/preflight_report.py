"""Cato-branded customer readiness report for preflight_check.py.

Turns the check's findings into a self-contained HTML document (fonts and the
official logo embedded, prints cleanly to A4/Letter) written for the customer:
plain language, grouped by who acts, with why each item matters and what to do.
The technical fixes for the migration engineers go in an appendix.

Optional PDF output uses a local Chrome/Chromium/Edge in headless mode.
"""
import base64
import hashlib
import html
import os
import re
import shutil
import subprocess
import time

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_assets")
LOGO_SHA256 = {
    "cato_logo_rgb_color_art_101.svg": "63d48dabae6e7ba66f5d6db207c853974b8e4716c60c5f17d68253c5322578b5",
    "cato_logo_rgb_white_art_101.svg": "bb0ba25208097457fcec91ac9672458b547443505d7a0b5f60d5dd47010134eb",
}

# Customer-facing labels (calm, plain; Coral and Sunflower carry meaning only).
LABELS = {
    "BLOCKER": ("Required before migration", "req"),
    "ACTION": ("To do or confirm", "todo"),
    "WARN": ("Review", "review"),
    "OK": ("Ready", "ok"),
    "INFO": ("Note", "note"),
}

# Who acts. Outline icons: 24 px grid, 2 px round stroke.
_I = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
OWNERS = [
    ("cato_team", "Your Cato account team", "Licenses, PoP entitlements and add-on services",
     f'<svg viewBox="0 0 24 24" {_I}><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 13h18"/></svg>'),
    ("cato_support", "Cato Support", "Features that need to be enabled on the account",
     f'<svg viewBox="0 0 24 24" {_I}><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M5.6 5.6l3.6 3.6M14.8 14.8l3.6 3.6M18.4 5.6l-3.6 3.6M9.2 14.8l-3.6 3.6"/></svg>'),
    ("identity", "Your identity team", "Users and groups from your identity provider",
     f'<svg viewBox="0 0 24 24" {_I}><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 20a6.5 6.5 0 0 0-3-5.5"/></svg>'),
    ("cma_admin", "Your Cato administrator", "Settings and objects in the Cato Management Application (CMA)",
     f'<svg viewBox="0 0 24 24" {_I}><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>'),
    ("migration", "Your migration team", "Preparation the migration engineers handle with you",
     f'<svg viewBox="0 0 24 24" {_I}><path d="M4 7h13l-3-3M20 17H7l3 3"/></svg>'),
]
ICON_CHECK = f'<svg viewBox="0 0 24 24" {_I}><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'

CMA_WHY = {
    "customCategory": "Custom categories group applications and domains for your policies. They can only be created in the CMA.",
    "device": "Device posture profiles define the device checks your access policies require. They can only be created in the CMA.",
    "bandwidthPriority": "Bandwidth priorities shape traffic in your WAN Network rules. Priorities beyond the built-in ones are created in the CMA.",
    "userNotification": "User notification templates are the messages people see when a rule blocks or prompts them.",
    "contentProfile": "Data Loss Prevention (DLP) content profiles define the data your Cato DLP rules look for.",
    "edmProfile": "Exact Data Match (EDM) profiles hold the data fingerprints your Cato DLP rules match against.",
    "rbiProfile": "Remote Browser Isolation (RBI) profiles control how isolated browsing behaves.",
}
SAME_NAMES = "In the CMA, create each item listed here with exactly the same name and settings as in your current account."


def _g(owner, title, why, steps=()):
    return {"owner": owner, "title": title, "why": why, "steps": list(steps)}


def guide(f):
    """Customer wording for a finding: owner, title, why it matters, what to do."""
    k, c, n = f["key"], f["ctx"], f["ctx"].get("n")
    rules = len(f["rules"])
    affects = f" {rules} of your policy rules depend on this." if rules else ""
    if k == "access_trial":
        return _g("cato_team", "Confirm license quantities for the new account",
                  "The new account is a trial, and trial accounts don't report license details. The quantities in this "
                  "section are what your current configuration uses.",
                  ["Share the license items in this section with your Cato account team and confirm each one is in place "
                   "before the migration date."])
    if k == "access_same_account":
        return _g("migration", "Choose a different target account", "The source and target are the same account.")
    if k in ("export_incomplete", "export_hosts_short", "export_no_rules"):
        why = {"export_incomplete": "Some configuration files are missing from the export, so parts of your configuration would not be copied.",
               "export_hosts_short": f"Only {c.get('have')} of your {c.get('need')} static hosts were exported.",
               "export_no_rules": "The export has no policy rules."}[k]
        return _g("migration", "Complete the configuration export", why,
                  ["Rerun the export, confirm it finishes for every object type, then run this check again."])
    if k in ("lic_ips_unknown", "lic_ips_short"):
        extra = f" The new account is licensed for {c.get('total')}." if k == "lic_ips_short" else ""
        return _g("cato_team", f"License {c['need']} public IP addresses",
                  f"Your current account uses {c['need']} dedicated public IP addresses at Cato PoPs (Points of Presence). "
                  f"Each one needs a license in the new account, or the addresses and the rules that use them can't be "
                  f"created.{extra}",
                  [f"Ask your Cato account team to license at least {c['need']} public IP addresses for the new account."])
    if k in ("lic_ztna_unknown", "lic_ztna_short"):
        extra = f" The new account is licensed for {c.get('total')}." if k == "lic_ztna_short" else ""
        return _g("cato_team", f"License {c['need']} remote users",
                  f"{c['need']} people connect remotely with the Cato Client today. Zero Trust Network Access (ZTNA) "
                  f"user licenses let them connect to the new account.{extra}",
                  [f"Ask your Cato account team to license at least {c['need']} ZTNA users."])
    if k in ("lic_sites_unknown", "lic_sites_none"):
        return _g("cato_team", f"License {c['need']} sites",
                  f"Your configuration has {c['need']} sites (Cato Socket, vSocket and IPSec). Each needs a site license or "
                  "a share of pooled bandwidth in the new account.",
                  [f"Ask your Cato account team to confirm the new account can hold {c['need']} sites."])
    if k in ("lic_skus_unknown", "lic_skus_missing"):
        return _g("cato_team", f"Match {n} add-on service{'s' if n != 1 else ''}",
                  "These services are active in your current account. Policies that rely on them behave differently, or "
                  "can't be created, without them.",
                  ["Review the list with your Cato account team and add the services you want to keep using."])
    if k == "pop_missing":
        return _g("cato_team", f"Make {n} PoP location{'s' if n != 1 else ''} available, or leave their items out",
                  f"Some dedicated IP addresses and IPSec tunnels use Cato PoPs ({', '.join(c.get('names', []))}) that "
                  "need a separate entitlement, such as PoPs in China." + affects,
                  ["Ask your Cato account team to add the entitlement for these PoPs.",
                   "Or agree with your migration team to leave these items out of the migration for now."])
    if k == "id_missing":
        t = c.get("type")
        if t == "MANUAL":
            ng = c.get("groups", n if "users" not in c else 0)
            nu = c.get("users", 0)
            what = " and ".join(x for x in (f"{ng} user group{'s' if ng != 1 else ''}" if ng else "",
                                            f"{nu} user{'s' if nu != 1 else ''}" if nu else "") if x)
            why = ("These were created by hand in your current account. The Cato API can't create user groups or "
                   "set group membership, so the groups need to be created in the Cato Management Application (CMA) "
                   "with exactly the same names and members." if ng else
                   "These users were created by hand in your current account, and your policy rules name them.")
            if nu:
                why += (" Users can be created in the CMA, or by your migration team through the Cato API."
                        if ng else " They can be created in the CMA, or by your migration team through the Cato API.")
            steps = []
            if nu:
                steps.append("Create each user listed here with the same name and email address, in the CMA or with "
                             "your migration team.")
            if ng:
                steps += ["In the CMA, create each group listed here with exactly the same name.",
                          "Add the same members to each group as in your current account."]
            return _g("cma_admin", f"Create {what}" + (" in the CMA" if ng and not nu else ""), why + affects, steps)
        if t == "SCIM":
            return _g("identity", f"Provision {n} users and groups through SCIM",
                      "Your policy rules refer to these users and groups by name. Your current account receives them from "
                      "your identity provider through SCIM (System for Cross-domain Identity Management), so the new "
                      "account needs the same." + affects,
                      ["Connect your identity provider to the new account's SCIM integration.",
                       "Assign the groups and users listed here, and run a sync.",
                       "Confirm they appear in the CMA before the migration starts."])
        if t == "LDAP":
            return _g("identity", f"Sync {n} users and groups from your directory",
                      "Your current account imports these from your directory (LDAP or Active Directory)." + affects,
                      ["Connect the new account to the same directory and sync these users and groups."])
        if t == "DYNAMIC":
            return _g("cma_admin", f"Turn on {n} dynamic user groups",
                      "These groups are built automatically from user attributes such as department." + affects,
                      ["Turn on dynamic user groups for the department attribute, in the CMA or with your migration "
                       "team through the Cato API.",
                       "Confirm the department attribute is filled in for the users these groups should hold."])
        return _g("migration", f"Review {n} users and groups that no longer exist",
                  "Your policy rules name these, but they aren't in your current account any more." + affects,
                  ["Decide with your migration team whether to tidy these rules before the migration."])
    if k == "id_scim_inactive":
        return _g("identity", "Connect your identity provider to the new account",
                  "Your current account receives users from an identity provider through SCIM. The new account has no "
                  "provisioned users yet.",
                  ["Create the SCIM integration in the new account.",
                   "Point your identity provider (for example Microsoft Entra ID or Okta) at it.",
                   "Assign the user groups your policies use, and run a sync."])
    if k == "id_ldap_inactive":
        return _g("identity", "Connect your directory to the new account",
                  "Your current account imports users from LDAP or Active Directory. The new account has none yet.",
                  ["Connect the new account to the directory and run a sync."])
    if k == "id_scim_endpoint_fail":
        return _g("identity", "Check the SCIM connection details",
                  "The SCIM address or token for the new account didn't work.",
                  ["Confirm the SCIM URL and token in the new account's SCIM integration and share them with your "
                   "migration team."])
    if k in ("cma_missing", "cma_unverified", "cma_undecided"):
        title = (f"Create {n} {c['one'] if n == 1 else c['many']} in the CMA" if k == "cma_missing"
                 else f"Confirm {n} {c['one'] if n == 1 else c['many']} exist in the CMA")
        return _g("cma_admin", title[0].upper() + title[1:], CMA_WHY.get(c.get("kind"), "") + affects, [SAME_NAMES])
    if k in ("feat_rbi_disabled", "feat_rbi_unverified"):
        return _g("cato_team", "Enable Cato RBI (Remote Browser Isolation)",
                  (f"{n} of your policy rules isolates" if n == 1 else f"{n} of your policy rules isolate")
                  + " risky browsing with Cato RBI"
                  + (", which isn't enabled in the new account." if k == "feat_rbi_disabled" else "."),
                  ["Ask your Cato account team to enable Cato RBI for the new account."])
    if k in ("feat_swp_fail", "feat_swp_unverified"):
        return _g("cato_support", "Enable Site Web Proxy for the new account",
                  f"{n} Site Web Proxy rule{'s' if n != 1 else ''} can't be created because the new account doesn't "
                  "accept Site Web Proxy rules yet.",
                  ["Open a case with Cato Support to enable Site Web Proxy for the new account, and mention the error "
                   "\u201cUnsupported field [addRule]\u201d."])
    if k == "feat_srt":
        return _g("cma_admin", "Turn on Static Range Translation",
                  (f"{n} of your network ranges translates addresses" if n == 1 else
                   f"{n} of your network ranges translate addresses")
                  + ". The new account accepts these translations only once Static Range Translation is turned on, "
                  "and that account setting can only be changed in the CMA.",
                  ["In the CMA, turn on Static Range Translation for the new account before the sites are created."])
    if k == "feat_l7":
        sites = ", ".join(c.get("sites") or [])
        return _g("cma_admin", f"Turn on LAN Layer 7 enforcement for {sites}",
                  (f"{n} LAN firewall rule matches" if n == 1 else f"{n} LAN firewall rules match")
                  + " on applications or services, which needs Layer 7 "
                  "enforcement on the site. It's a per-site setting that can only be changed in the CMA.",
                  ["After the sites exist in the new account, turn on LAN Layer 7 enforcement for the sites listed."])
    if k == "feat_waf_range":
        return _g("cma_admin", f"Create {n} IP range{'s' if n != 1 else ''} in the CMA",
                  "This range includes 169.254.169.254, an address the Cato API doesn't accept for security reasons, so "
                  "it needs to be created by hand." + affects,
                  ["In the CMA, create the range listed here with the same name and value."])
    if k == "feat_wifi":
        return _g("migration", f"Provide passwords for {n} Wi-Fi network{'s' if n != 1 else ''}",
                  "Wi-Fi passwords aren't included when configuration is exported.",
                  ["Share the passwords with your migration team securely, or plan to set them after the migration."])
    if k == "feat_webhooks":
        return _g("migration", f"Decide where {n} webhook{'s' if n != 1 else ''} should send events",
                  "Webhooks copy across pointing at the same destinations as today, such as your ticketing or chat tools.",
                  ["Confirm each destination with your migration team, and have the credentials ready."])
    if k == "feat_ipsec":
        return _g("migration", f"Prepare IPSec tunnel details for {n} site{'s' if n != 1 else ''}",
                  "Tunnel settings and pre-shared keys aren't included in the export.",
                  ["Gather the tunnel details and keys for the sites listed, ready for after the sites are created."])
    if k == "int_classic":
        return _g("migration", f"Recreate {n} legacy group{'s' if n != 1 else ''}",
                  "These groups use an older format that the Cato API can't read, so their members need to be recreated "
                  "as groups in the new account." + affects,
                  ["Your migration team will copy each group's members from your current account."])
    if k == "int_stale":
        return _g("migration", f"Review {n} reference{'s' if n != 1 else ''} to deleted objects",
                  "Some rules still point at sites or ranges that no longer exist in your current account." + affects,
                  ["Decide with your migration team whether to tidy these rules before the migration."])
    if k == "tgt_clash":
        return _g("migration", f"Clear {n} sample rule{'s' if n != 1 else ''} from the new account",
                  "New accounts include sample rules. Some share names with your rules, which would create duplicates.",
                  ["Your migration team will review the sample rules and remove them before the migration."])
    if k == "tgt_existing_named":
        return _g("migration", f"Review {n} {c.get('label')} that already exist in the new account",
                  "A fresh migration expects these to be absent.",
                  ["Decide with your migration team whether to adopt or remove them."])
    if k in ("probe_failed", "probe_baseline", "tgt_unreadable", "tgt_rules_existing"):
        return _g("migration", f["title"], f["detail"] or "", [])
    return _g("migration", f["title"], f["detail"] or "", [])


# OK findings, phrased for the "already in place" list.
def ready_text(f):
    k, c = f["key"], f["ctx"]
    return {
        "export_ok": f"Your configuration export is complete ({c.get('rules')} policy rules across {c.get('policies')} policies).",
        "export_hosts_ok": f"All {c.get('n')} static hosts were exported.",
        "lic_ips_ok": f"The new account has enough public IP address licenses ({c.get('need')} needed).",
        "lic_ztna_ok": f"The new account has enough ZTNA user licenses ({c.get('need')} needed).",
        "pop_summary": f"{c.get('ok')} of {c.get('n')} Cato PoPs your configuration uses are available to the new account.",
        "id_ok": f"All {c.get('n')} users and user groups your rules name exist in the new account.",
        "id_scim_endpoint_ok": "The new account's SCIM integration answers.",
        "cma_present": f"{c.get('n')} {c.get('one') if c.get('n') == 1 else c.get('many')} your rules use already exist in the new account.",
        "feat_rbi_ok": "Cato RBI is enabled in the new account.",
        "feat_swp_ok": "Site Web Proxy rules can be created in the new account.",
        "feat_srt_ok": "Your network ranges don't need Static Range Translation.",
        "int_classic_done": f"{c.get('n')} legacy groups have already been recreated for the migration.",
        "int_ok": "Every site, range, application and IP address your rules name is in the export.",
    }.get(k, f["title"])


# --------------------------------------------------------------------------
def _asset(name, binary=True):
    path = os.path.join(ASSETS, name)
    with open(path, "rb") as fh:
        data = fh.read()
    return data if binary else data.decode()


def _logo(name):
    try:
        data = _asset(name)
    except OSError:
        return ""
    if hashlib.sha256(data).hexdigest() != LOGO_SHA256.get(name):
        return ""  # never show modified artwork
    # As an image, not inline SVG: the kit's files share class names (.st0 ...), and two
    # inline copies would restyle each other.
    return f'<img class="cato-logo" alt="Cato Networks" src="data:image/svg+xml;base64,{base64.b64encode(data).decode()}">'


def _font_face():
    out = []
    for fam, weight, fn, fmt in [("Archivo", "100 900", "archivo-latin-wght-normal.woff2", "woff2"),
                                 ("Inter", "400", "Inter-Regular.woff", "woff"), ("Inter", "700", "Inter-Bold.woff", "woff")]:
        try:
            b64 = base64.b64encode(_asset(fn)).decode()
        except OSError:
            continue
        out.append(f"@font-face{{font-family:'{fam}';font-weight:{weight};font-style:normal;font-display:swap;"
                   f"src:url(data:font/{fmt};base64,{b64}) format('{fmt}');}}")
    return "\n".join(out)


def _ring(pct):
    """Flat 2D open ring as a readiness gauge (Cato Green on Pale Grey)."""
    r, w = 70, 18
    circ = 2 * 3.14159265 * r
    dash = max(0.0, min(1.0, pct)) * circ
    return (f'<svg viewBox="0 0 180 180" class="ring" role="img" aria-label="{round(pct * 100)} percent ready">'
            f'<circle cx="90" cy="90" r="{r}" fill="none" stroke="#F0F0F0" stroke-width="{w}"/>'
            f'<circle cx="90" cy="90" r="{r}" fill="none" stroke="#128461" stroke-width="{w}" '
            f'stroke-dasharray="{dash:.1f} {circ:.1f}" transform="rotate(-90 90 90)"/>'
            f'<text x="90" y="88" text-anchor="middle" class="ring-num">{round(pct * 100)}%</text>'
            f'<text x="90" y="112" text-anchor="middle" class="ring-sub">ready now</text></svg>')


CSS = """
:root{--green:#128461;--moss:#006F53;--forest:#003E34;--pine:#002C28;--hl:#00FFCC;--coral:#FF3749;--sun:#FFC820;
--pale:#F0F0F0;--light:#C8C8C8;--grey:#969696;--mid:#646464;--dark:#464646;--black:#000}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:#fff;color:#1b1b1b;font:15px/1.6 Inter,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
.page{max-width:920px;margin:0 auto;padding:0 40px 48px}
h1,h2{font-family:Archivo,Inter,sans-serif;font-weight:500;letter-spacing:-.01em;margin:0}
h2{font-size:26px;color:var(--pine);margin:44px 0 6px}
h3{font-size:16px;font-weight:700;margin:0 0 4px}
p{margin:0 0 10px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--moss)}
.hero{background:linear-gradient(125deg,#128461,#00392F);color:#fff;padding:34px 40px 40px;border-radius:0 0 20px 20px;
max-width:1000px;margin:0 auto}
.hero .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:46px}
.hero .logo img{height:34px;width:auto;display:block}
.hero .eyebrow{color:var(--hl)}
.hero h1{font-size:40px;line-height:1.15;margin:10px 0 14px;max-width:720px}
.hero .meta{display:flex;flex-wrap:wrap;gap:6px 28px;font-size:14px;color:#dff5ec}
.hero .meta b{color:#fff;font-weight:700}
.pill{display:inline-block;padding:6px 14px;border-radius:999px;font-weight:700;font-size:13px;background:#fff;color:var(--forest)}
.summary{display:grid;grid-template-columns:200px 1fr;gap:32px;align-items:center;margin:36px 0 8px}
.ring{width:190px;height:190px}.ring-num{font:500 40px Archivo,Inter,sans-serif;fill:var(--pine)}
.ring-sub{font:600 12px Inter,sans-serif;fill:var(--mid);letter-spacing:.08em;text-transform:uppercase}
.lead{font-size:18px;line-height:1.55;color:#222;margin-bottom:16px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{border:1px solid #e3e3e3;border-left:5px solid var(--grey);border-radius:10px;padding:10px 12px}
.stat .num{font:500 28px Archivo,Inter,sans-serif;color:var(--pine);line-height:1.1}
.stat .lbl{font-size:12.5px;color:var(--dark)}
.stat.req{border-left-color:var(--coral)}.stat.todo{border-left-color:var(--sun)}.stat.ok{border-left-color:var(--green)}
.owner{margin-top:30px}
.owner-head{display:flex;gap:14px;align-items:center;padding-bottom:10px;border-bottom:2px solid var(--green);margin-bottom:14px;break-after:avoid}
.owner-head .ico{width:42px;height:42px;border-radius:50%;background:var(--green);color:#fff;display:flex;align-items:center;justify-content:center;flex:none}
.owner-head .ico svg{width:22px;height:22px}
.owner-head .count{margin-left:auto;font-size:13px;color:var(--mid);white-space:nowrap}
.owner-head h3{font-family:Archivo,Inter,sans-serif;font-weight:500;font-size:20px;color:var(--pine);margin:0}
.owner-head .sub{font-size:13px;color:var(--mid)}
.item{display:grid;grid-template-columns:22px 1fr;gap:12px;padding:14px 16px;border:1px solid #e6e6e6;border-radius:12px;margin-bottom:10px;break-inside:avoid}
.box{width:18px;height:18px;border:2px solid var(--dark);border-radius:4px;margin-top:3px}
.no{display:inline-block;min-width:22px;height:22px;line-height:22px;text-align:center;border-radius:11px;background:var(--forest);color:#fff;font-size:12px;font-weight:700;margin-right:8px;padding:0 6px}
.rules-by-policy{columns:2;column-gap:28px;font-size:12.5px;margin-top:10px}
.rules-by-policy .pol{break-inside:avoid;margin-bottom:10px}
.rules-by-policy .pol b{display:block;color:var(--pine);font-size:13px;margin-bottom:2px}
.rules-by-policy .r{display:flex;justify-content:space-between;gap:10px;padding:1px 0;border-bottom:1px dotted #ddd}
.rules-by-policy .r span:last-child{color:var(--mid);white-space:nowrap}
.chip{display:inline-block;font-size:11.5px;font-weight:700;letter-spacing:.04em;padding:2px 9px;border-radius:999px;margin-bottom:6px}
.chip.req{background:#ffe3e6;color:#8a0f1a}.chip.todo{background:#fff2c6;color:#5c4500}.chip.review{background:var(--pale);color:var(--dark)}
.item .why{color:#333}
.item ol{margin:6px 0 4px 18px;padding:0}.item li{margin:2px 0}
.what{font-size:12px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--moss);margin-top:8px}
.names{margin:8px 0 0;padding:8px 12px;background:var(--pale);border-radius:8px;font-size:13px;color:#2a2a2a;columns:2;column-gap:26px}
.names div{break-inside:avoid;padding:1px 0;overflow-wrap:anywhere}
.more{font-size:12.5px;color:var(--mid);margin-top:4px}
.affects{font-size:12.5px;color:var(--mid);margin-top:6px}
.ready{display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;margin-top:12px}
.ready div{display:flex;gap:10px;align-items:flex-start;font-size:14px}
.ready svg{width:20px;height:20px;color:var(--green);flex:none;margin-top:2px}
.note{background:var(--pale);border-radius:12px;padding:16px 20px;margin-top:12px}
.note li{margin:4px 0}
.appendix{break-before:page}
.appendix h2{margin-top:28px}
.app-item{border-top:1px solid #e3e3e3;padding:10px 0;break-inside:avoid}
.app-item .t{font-weight:700}
.app-rules{font-size:12.5px;columns:2;column-gap:26px;color:#333;margin-top:4px}
.app-rules div{overflow-wrap:anywhere;break-inside:avoid}
.code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:#333;overflow-wrap:anywhere}
footer{margin-top:48px;padding-top:16px;border-top:1px solid #e3e3e3;display:flex;gap:18px;align-items:center;font-size:12px;color:var(--mid)}
footer img{height:20px;width:auto;flex:none}
@page{size:A4;margin:14mm 12mm}
@media print{body{font-size:12.5px;line-height:1.5}.hero{border-radius:0;padding:26px 30px 30px}.hero .top{margin-bottom:28px}
.hero h1{font-size:32px}.page{padding:0 4px 20px}h2{font-size:22px;margin-top:24px}.lead{font-size:15px}
.summary{margin-top:22px;grid-template-columns:170px 1fr;gap:24px}.ring{width:160px;height:160px}
.owner{margin-top:20px}.owner-head{margin-bottom:10px}.item{padding:10px 12px;margin-bottom:8px}
.item ol{margin-top:2px}.names{font-size:11.5px;padding:6px 10px}.rules-by-policy{font-size:11px}
.app-item{padding:7px 0}.code{font-size:11px}}
"""


def _esc(s):
    return html.escape(str(s), quote=True)


FAMILY_NAMES = {
    "internetFirewall": "Internet Firewall", "wanFirewall": "WAN Firewall", "wanNetwork": "WAN Network",
    "applicationControl": "Application Control", "tlsInspect": "TLS Inspection", "socketBypass": "Socket Bypass",
    "socketLan": "LAN Firewall", "clientConnectivity": "Client Connectivity", "privateAccess": "Private Access",
    "splitTunnel": "Split Tunnel", "appTenantRestriction": "App Tenant Restriction", "ztnaAlwaysOn": "Always-On",
    "dynamicIpAllocation": "Dynamic IP Allocation", "remotePortFwd": "Remote Port Forwarding",
    "sdpStaticIpAllocation": "Static IP Allocation", "siteWebProxy": "Site Web Proxy",
    "antiMalwareFileHash": "Anti-Malware File Hash", "terminalServer": "Terminal Server",
}
_FAM_RE = re.compile(r"\b(" + "|".join(FAMILY_NAMES) + r"): ")


def _friendly(text, key=None):
    """Customer-facing form of an engineering list entry."""
    t = _FAM_RE.sub(lambda m: FAMILY_NAMES[m.group(1)] + ": ", str(text))
    t = re.sub(r"\b1 rule\(s\)", "1 rule", t)
    t = t.replace("rule(s)", "rules").replace(" :: ", " / ")
    if key in ("lic_skus_unknown", "lic_skus_missing"):
        m = re.match(r"^(\S+) \((.+)\)$", t)
        if m:
            t = f"{m.group(2)[0].upper()}{m.group(2)[1:]} ({m.group(1)})"
    return t


def render_customer_html(rep, exp, meta, customer=None):
    total = len(exp["rules"])
    blocked = len({r for f in rep.findings if f["severity"] == "BLOCKER" for r in f["rules"]})
    at_risk = len({r for f in rep.findings if f["severity"] in ("BLOCKER", "ACTION") for r in f["rules"]})
    ready_now = max(0, total - at_risk)
    pct = ready_now / total if total else 1.0
    counts = rep.counts()
    if counts["BLOCKER"]:
        status = "Preparation needed"
        lead = (f"{ready_now} of your {total} policy rules can move to the new account as they are. Before the migration "
                f"starts, {counts['BLOCKER']} item{'s' if counts['BLOCKER'] != 1 else ''} need to be in place and "
                f"{counts['ACTION']} more need doing or confirming. Each one says who acts, why it matters and what to do.")
    elif counts["ACTION"]:
        status = "Nearly ready"
        lead = (f"Nothing stands in the way of the migration. {counts['ACTION']} item{'s' if counts['ACTION'] != 1 else ''} "
                "need doing or confirming first, listed below with who acts and what to do.")
    else:
        status = "Ready to migrate"
        lead = "Everything this check can verify is in place. Review the notes below, then schedule the migration."

    actionable = [f for f in rep.findings if f["severity"] in ("BLOCKER", "ACTION", "WARN")]
    by_owner = {o[0]: [] for o in OWNERS}
    for f in actionable:
        g = guide(f)
        by_owner.setdefault(g["owner"], []).append((f, g))
    order = {"BLOCKER": 0, "ACTION": 1, "WARN": 2}

    head_meta = ([("Prepared for", customer)] if customer else []) + [
                 ("Current account", meta["source"]), ("New account", meta["target"]),
                 ("Checked", meta["when"] + (", including live verification" if meta.get("probe") else ""))]
    parts = [f"""<!DOCTYPE html><html lang="en-US"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Migration readiness report: {_esc(meta['target'])}</title>
<!-- Fonts: Archivo and Inter, SIL Open Font License 1.1. Logo: official Cato artwork, unmodified. -->
<style>{_font_face()}{CSS}</style></head><body>
<header class="hero"><div class="top"><div class="logo">{_logo('cato_logo_rgb_white_art_101.svg')}</div>
<span class="pill">{_esc(status)}</span></div>
<div class="eyebrow">Migration readiness report</div>
<h1>Getting your new Cato account ready for migration</h1>
<div class="meta">{''.join(f'<span>{_esc(k)}: <b>{_esc(v)}</b></span>' for k, v in head_meta)}</div></header>
<main class="page">
<section class="summary">{_ring(pct)}<div><p class="lead">{_esc(lead)}</p>
<div class="stats">
<div class="stat req"><div class="num">{counts['BLOCKER']}</div><div class="lbl">Required before migration</div></div>
<div class="stat todo"><div class="num">{counts['ACTION']}</div><div class="lbl">To do or confirm</div></div>
<div class="stat"><div class="num">{counts['WARN']}</div><div class="lbl">To review</div></div>
<div class="stat ok"><div class="num">{counts['OK']}</div><div class="lbl">Already in place</div></div>
</div></div></section>
<p class="affects">{blocked} of {total} policy rules wait on an item required before migration; {at_risk - blocked} more
wait on an item to do or confirm.</p>
<h2>Your action plan</h2>
<p>Grouped by who acts. Tick each item as it's done, then run the check again to confirm.</p>"""]

    item_no, seq = {}, 0
    for key, _, _, _ in OWNERS:
        for f, _ in sorted(by_owner.get(key, []), key=lambda x: order[x[0]["severity"]]):
            seq += 1
            item_no[id(f)] = seq
    for key, name, sub, icon in OWNERS:
        items = sorted(by_owner.get(key, []), key=lambda x: order[x[0]["severity"]])
        if not items:
            continue
        n_req = sum(1 for f, _ in items if f["severity"] == "BLOCKER")
        parts.append(f'<section class="owner"><div class="owner-head"><div class="ico">{icon}</div>'
                     f'<div><h3>{_esc(name)}</h3><div class="sub">{_esc(sub)}</div></div>'
                     f'<div class="count">{len(items)} item{"s" if len(items) != 1 else ""}'
                     f'{f" &middot; {n_req} required" if n_req else ""}</div></div>')
        for f, g in items:
            label, cls = LABELS[f["severity"]]
            steps = "".join(f"<li>{_esc(s)}</li>" for s in g["steps"])
            names = f["items"][:16]
            more = len(f["items"]) - len(names)
            parts.append(
                f'<div class="item"><div class="box"></div><div>'
                f'<span class="chip {cls}">{_esc(label)}</span><h3><span class="no">{item_no[id(f)]}</span>{_esc(g["title"])}</h3>'
                f'<p class="why">{_esc(g["why"])}</p>'
                + (f'<div class="what">What to do</div><ol>{steps}</ol>' if steps else "")
                + (f'<div class="names">{"".join(f"<div>{_esc(_friendly(x, f['key']))}</div>" for x in names)}</div>' if names else "")
                + (f'<div class="more">and {more} more, listed in the appendix</div>' if more > 0 else "")
                + "</div></div>")
        parts.append("</section>")

    ok = [f for f in rep.findings if f["severity"] == "OK"]
    if ok:
        parts.append('<h2>Already in place</h2><div class="ready">'
                     + "".join(f"<div>{ICON_CHECK}<span>{_esc(ready_text(f))}</span></div>" for f in ok) + "</div>")

    parts.append("""<h2>What this check can't confirm for you</h2>
<div class="note"><p>The Cato API doesn't expose a few settings, so please confirm these directly:</p><ul>
<li>Your identity provider's SCIM or directory settings (the check sees only the users and groups they create).</li>
<li>Whether Static Range Translation is turned on for the account, if your network ranges translate addresses (the check sees each range's translation, not the account setting).</li>
<li>LAN Layer 7 enforcement on each site.</li>
<li>License quantities on a trial account, which doesn't report them.</li></ul></div>""")

    # Appendix A: each affected rule once, with the numbered items it waits on.
    waits = {}
    for f in actionable:
        for r in f["rules"]:
            waits.setdefault(r, set()).add(item_no[id(f)])
    parts.append('<section class="appendix"><div class="eyebrow">Appendix A</div><h2>Policy rules and what they wait on</h2>'
                 f'<p>{len(waits)} of your {total} policy rules wait on at least one item. The numbers match the items in '
                 'your action plan.</p><div class="rules-by-policy">')
    by_pol = {}
    for r in waits:
        fam, _, name = r.partition(": ")
        by_pol.setdefault(FAMILY_NAMES.get(fam, fam), []).append((name, waits[r]))
    for pol in sorted(by_pol):
        rows = "".join(f'<div class="r"><span>{_esc(_friendly(n))}</span><span>{", ".join(str(x) for x in sorted(w))}</span></div>'
                       for n, w in sorted(by_pol[pol], key=lambda x: x[0].lower()))
        parts.append(f'<div class="pol"><b>{_esc(pol)}</b>{rows}</div>')
    parts.append("</div>")
    long_lists = [(f, guide(f)) for f in actionable if len(f["items"]) > 16]
    if long_lists:
        parts.append('<h2>Full lists</h2>')
        for f, g in long_lists:
            parts.append(f'<div class="app-item"><div class="t">{item_no[id(f)]}. {_esc(g["title"])}</div>'
                         f'<div class="app-rules">{"".join(f"<div>{_esc(_friendly(x, f["key"]))}</div>" for x in f["items"])}</div></div>')
    parts.append('<div class="eyebrow" style="margin-top:34px">Appendix B</div><h2>Technical notes for the migration team</h2>')
    for f in sorted(actionable, key=lambda x: order[x["severity"]]):
        if not (f["fix"] or f["detail"]):
            continue
        parts.append(f'<div class="app-item"><div class="t">{_esc(f["title"])}</div>'
                     + (f'<div class="code">{_esc(f["detail"])}</div>' if f["detail"] else "")
                     + (f'<div class="code">{_esc(f["fix"])}</div>' if f["fix"] else "") + "</div>")
    parts.append("</section>")
    parts.append(f'<footer>{_logo("cato_logo_rgb_color_art_101.svg")}<div>Generated by the migration readiness check '
                 f'(preflight_check.py) on {_esc(meta["when"])}. This report contains configuration names from your '
                 'accounts; share it only with the people working on your migration.</div></footer></main></body></html>')
    return "\n".join(parts)


def find_browser():
    for p in [os.environ.get("CHROME_PATH"),
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              shutil.which("google-chrome"), shutil.which("google-chrome-stable"), shutil.which("chromium"),
              shutil.which("chromium-browser"), shutil.which("msedge"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"]:
        if p and os.path.exists(p):
            return p
    return None


def html_to_pdf(html_path, pdf_path):
    """Print the report to PDF with a local headless Chrome/Edge. Returns an error string or None."""
    browser = find_browser()
    if not browser:
        return "no Chrome, Chromium or Edge found (set CHROME_PATH)"
    url = "file://" + os.path.abspath(html_path)
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
           f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if not os.path.exists(pdf_path) or os.path.getmtime(pdf_path) < time.time() - 120:
        return (r.stderr or "PDF was not written")[-300:]
    return None
