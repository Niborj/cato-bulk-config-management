#!/usr/bin/env python3
"""Before-you-migrate dependency check for a Cato tenant-to-tenant migration.

Compares a SOURCE tenant (its catocli export plus live, read-only API reads)
with a TARGET tenant (live reads) and lists everything that has to be enabled,
licensed, created or decided BEFORE `bulk_apply.py` runs, so a migration does
not discover it one failed apply at a time.

What it checks
  access      both tenants reachable, not the same account, plan type
  export      the source export is complete (types present, static hosts on
              every site type, no stripped-ids surprises)
  licensing   public (allocated) IP capacity, ZTNA users, site licenses,
              add-on SKUs the source has and the target lacks
  pops        every PoP the source allocates IPs on (and rules pin) exists for
              the target (China and private PoPs need their own entitlement)
  identity    SCIM / LDAP / manual users and user groups the rules name:
              present in the target or not, how they get there, whether a SCIM
              integration looks active, and (with scim_url/scim_token in the
              target profile) whether the SCIM endpoint answers
  cma-only    objects the API can reference but not create or list: custom
              categories, device posture profiles, bandwidth priorities, user
              notification templates, DLP content/EDM profiles, RBI profiles
  features    RBI, Site Web Proxy, Static Range Translation, LAN Layer 7,
              the 169.254.169.254 range the API firewall blocks, Wi-Fi
              passwords, webhook endpoints, IPSec pre-shared keys
  integrity   source rules pointing at classic groups (invisible to the API)
              or at objects that no longer exist in the source
  target      factory/existing rules whose names clash with the source,
              existing sites/groups/IPs, policy on/off differences

Read-only by default. `--probe` additionally verifies the objects and features
the API cannot list by trying them inside a throwaway, named draft revision on
the TARGET (Internet Firewall, WAN Network, App Control, Site Web Proxy) and
discarding the revision straight away: nothing is published, and the live
policy never changes. It needs a target key with edit rights and is refused
for an account listed as a protected source.

Usage
  python3 scripts/preflight_check.py --source-profile <source> --target-profile <target>
  python3 scripts/preflight_check.py --source-profile <source> --target-profile <target> --probe
  python3 scripts/preflight_check.py ... --config-dir config_data --out-dir preflight --strict

Exit code: 2 = at least one BLOCKER, 1 = ACTION items only (with --strict),
0 = nothing that stops a migration. Reports land in --out-dir (gitignored:
tenant data): preflight_report.md and .json for engineers, and
preflight_report.html, a Cato-branded readiness report written for the
customer (add --pdf for a PDF copy and --customer "Name" for the cover).
"""
import argparse
import copy
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cato_profile import REPO_ROOT, get_profile, load_protected  # noqa: E402
from preflight_report import html_to_pdf, render_customer_html  # noqa: E402

DEFAULT_ENDPOINT = "https://api.catonetworks.com/api/v1/graphql2"
SEVERITIES = ["BLOCKER", "ACTION", "WARN", "INFO", "OK"]
SEV_ICON = {"BLOCKER": "[X]", "ACTION": "[!]", "WARN": "[~]", "INFO": "[i]", "OK": "[ok]"}

# Bandwidth management priorities every tenant has out of the box.
DEFAULT_BANDWIDTH_PRIORITIES = {"10", "20", "30", "40", "255"}

# Reference kinds (keys inside rule JSON whose values are {name} refs).
IDENTITY_KINDS = {"usersGroup", "user"}
CMA_ONLY_KINDS = {  # kind: (singular, plural)
    "customCategory": ("custom category", "custom categories"),
    "device": ("device posture profile", "device posture profiles"),
    "bandwidthPriority": ("bandwidth management priority", "bandwidth management priorities"),
    "userNotification": ("user notification template", "user notification templates"),
    "contentProfile": ("DLP content profile", "DLP content profiles"),
    "edmProfile": ("DLP EDM profile", "DLP EDM profiles"),
    "rbiProfile": ("RBI profile", "RBI profiles"),
}
INTEGRITY_KINDS = {"group", "site", "backhaulingSite", "associatedSite", "siteNetworkSubnet",
                   "customApp", "globalIpRange", "host", "allocationIp", "externalIp", "popLocation"}
REF_KINDS = IDENTITY_KINDS | set(CMA_ONLY_KINDS) | INTEGRITY_KINDS

# GraphQL policy family names, in the order bulk_apply deploys them.
POLICY_FAMILIES = [
    "internetFirewall", "wanFirewall", "wanNetwork", "applicationControl", "tlsInspect",
    "socketBypass", "socketLan", "clientConnectivity", "privateAccess", "splitTunnel",
    "appTenantRestriction", "ztnaAlwaysOn", "dynamicIpAllocation", "remotePortFwd",
    "sdpStaticIpAllocation", "siteWebProxy", "antiMalwareFileHash", "terminalServer",
]

# Export files the bulk folders read (catocli export all + static hosts + webhooks).
EXPECTED_EXPORT_FILES = [
    "socket_sites.json", "vsocket_sites.json", "ipsec_sites.json", "groups.json",
    "global_ip_ranges.json", "allocated_ips.json", "static_hosts.json", "webhooks.json",
    "custom_applications.json",
]

SKU_FEATURES = {
    "CATO_CASB": "App Control (CASB) rules", "CATO_DLP": "DLP data rules",
    "CATO_RBI": "Remote Browser Isolation", "CATO_IP_ADD": "allocated (public) IPs",
    "CATO_ZTNA_USERS": "ZTNA / SDP users", "CATO_THREAT_PREVENTION": "threat prevention",
    "CATO_THREAT_PREVENTION_ADV": "advanced threat prevention", "CATO_EPP": "endpoint protection",
    "CATO_SITE": "site licenses", "CATO_PB": "pooled bandwidth", "CATO_IOT_OT": "IoT/OT device attributes",
    "CATO_AGTP": "agentic threat prevention", "CATO_AI_SEC_U": "AI security (users)",
    "CATO_AI_SEC_APP_U": "AI security (applications)", "CATO_ASTS_SEC_1_5K": "assets security",
    "CATO_DATALAKE": "data lake / event retention", "CATO_DEM": "digital experience monitoring",
    "CATO_SAAS_SECURITY_API": "SaaS security API (CASB API)", "CATO_XDR_PRO": "XDR Pro", "CATO_XOPS": "XOps",
    "CATO_ANTI_MALWARE_NG": "next-gen anti-malware", "CATO_IPS": "IPS", "CATO_MDR": "MDR",
}


class ApiError(Exception):
    pass


class Api:
    """Minimal GraphQL client on a catocli profile (token never printed)."""

    def __init__(self, profile):
        p = get_profile(profile)
        self.profile = profile
        self.account = str(p["account_id"])
        self.endpoint = p.get("endpoint") or DEFAULT_ENDPOINT
        self._token = p["cato_token"]
        self.scim_url = p.get("scim_url")
        self._scim_token = p.get("scim_token")

    def q(self, query, variables=None, allow_errors=False):
        body = json.dumps({"query": query, "variables": dict(variables or {}, a=self.account)}).encode()
        last = None
        for attempt in range(4):
            req = urllib.request.Request(self.endpoint, data=body, headers={
                "Content-Type": "application/json", "x-api-key": self._token,
                "User-Agent": "cato-bulk-config-management/preflight"})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code not in (429, 500, 502, 503, 504):
                    raise ApiError(f"{self.profile}: {last}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = str(e)
            time.sleep(2 * (attempt + 1))
        else:
            raise ApiError(f"{self.profile}: request failed after retries ({last})")
        if data.get("errors") and not allow_errors:
            raise ApiError(f"{self.profile}: {data['errors'][0].get('message', data['errors'])}")
        return data

    def scim_get(self, path):
        req = urllib.request.Request(self.scim_url.rstrip("/") + path, headers={
            "Authorization": f"Bearer {self._scim_token}", "Accept": "application/scim+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")


# --------------------------------------------------------------------------
# Live tenant state
# --------------------------------------------------------------------------
Q_ACCOUNT = """query q($a:ID!){
  accountManagement(accountId:$a){ account{ id name type tenancy plan status } }
  licensing(accountId:$a){ licensingInfo{
    licenses{ sku status plan ... on SiteLicense { total } ... on PooledBandwidthLicense { total }
              ... on ZtnaUsersLicense { total } ... on PublicIpsLicense { total } }
    globalLicenseAllocations{ publicIps{ total allocated available } ztnaUsers{ total allocated available } } } }
  popLocations(accountId:$a){ allocatedIpList(input:{}){ allocatedIp{ ip description popLocation{ id name } } } }
}"""


def _paged(api, query, path, limit=500, key_items="items", key_total=("pageInfo", "total")):
    out, start = [], 0
    while True:
        d = api.q(query, {"limit": limit, "from": start})["data"]
        node = d
        for p in path:
            node = node[p]
        items = node.get(key_items) or []
        out.extend(items)
        total = node
        for k in key_total:
            total = (total or {}).get(k)
        start += limit
        if not items or start >= (total or 0):
            return out


def load_tenant(api, with_policies=True):
    t = {"profile": api.profile, "account_id": api.account}
    d = api.q(Q_ACCOUNT)["data"]
    t["account"] = (d.get("accountManagement") or {}).get("account") or {}
    li = ((d.get("licensing") or {}).get("licensingInfo") or {})
    t["licenses"] = li.get("licenses") or []
    t["allocations"] = li.get("globalLicenseAllocations") or {}
    t["allocated_ips"] = ((d.get("popLocations") or {}).get("allocatedIpList") or {}).get("allocatedIp") or []

    locs = api.q('query q($a:ID!){ entityLookup(accountID:$a, type: location, limit: 5000){ items{ entity{ id name } } } }')
    t["locations"] = {i["entity"]["name"] for i in locs["data"]["entityLookup"]["items"]}
    sites = api.q('query q($a:ID!){ entityLookup(accountID:$a, type: site, limit: 5000){ items{ entity{ id name } } } }')
    t["sites"] = {i["entity"]["name"] for i in sites["data"]["entityLookup"]["items"]}
    hosts = api.q('query q($a:ID!){ entityLookup(accountID:$a, type: host, limit: 1){ total } }')
    t["host_total"] = hosts["data"]["entityLookup"]["total"]

    t["users"] = _paged(api, """query q($a:ID!,$limit:Int!,$from:Int!){ user(accountId:$a){ userList(input:{paging:{limit:$limit,from:$from}}){
        items{ displayName firstName lastName email userImportType directoryId } pageInfo{ total } } } }""", ["user", "userList"])
    t["user_groups"] = _paged(api, """query q($a:ID!,$limit:Int!,$from:Int!){ user(accountId:$a){ usersGroupList(input:{paging:{limit:$limit,from:$from}}){
        items{ name importType directoryId membersCount } paging{ total } } } }""", ["user", "usersGroupList"], key_total=("paging", "total"))
    t["groups"] = {g["name"] for g in _paged(api, """query q($a:ID!,$limit:Int!,$from:Int!){ groups(accountId:$a){ groupList(input:{paging:{limit:$limit,from:$from}, sort:{}}){
        items{ name } paging{ total } } } }""", ["groups", "groupList"], limit=200, key_total=("paging", "total"))}

    t["policies"] = {}
    if with_policies:
        for fam in POLICY_FAMILIES:
            r = api.q("query q($a:ID!){ policy(accountId:$a){ %s{ policy{ enabled rules{ rule{ name } } } } } }" % fam, allow_errors=True)
            if r.get("errors") or not r.get("data"):
                t["policies"][fam] = {"error": (r.get("errors") or [{}])[0].get("message", "no data")}
                continue
            p = r["data"]["policy"][fam]["policy"]
            t["policies"][fam] = {"enabled": p.get("enabled"), "rules": [x["rule"]["name"] for x in p.get("rules") or []]}
    return t


# --------------------------------------------------------------------------
# Source export
# --------------------------------------------------------------------------
def _load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _display_path(path):
    rel = os.path.relpath(os.path.abspath(path), REPO_ROOT)
    return path if rel.startswith("..") else rel


def load_export(config_dir):
    e = {"dir": _display_path(config_dir), "rules": [], "policy_enabled": {}, "missing_files": []}
    for f in EXPECTED_EXPORT_FILES:
        if not os.path.exists(os.path.join(config_dir, f)):
            e["missing_files"].append(f)
    policy_files = sorted(glob.glob(os.path.join(config_dir, "all_*_rules_and_sections.json")))
    if not policy_files:
        e["missing_files"].append("all_*_rules_and_sections.json")
    for f in policy_files:
        d = _load(f, {})
        pol = (d.get("data") or {}).get("policy") or {}
        if not pol:
            continue
        fam = next(iter(pol))
        p = pol[fam].get("policy") or {}
        e["policy_enabled"][fam] = p.get("enabled")
        for rw in p.get("rules") or []:
            r = rw.get("rule", rw)
            e["rules"].append({"family": fam, "name": r.get("name"), "raw": r})
        for nw in p.get("network_rules") or []:
            nr = nw.get("rule", nw)
            e["rules"].append({"family": fam, "name": nr.get("name"), "raw": nr})
            for fw in nw.get("firewall_rules") or []:
                fr = fw.get("rule", fw)
                e["rules"].append({"family": fam, "name": f'{nr.get("name")} :: {fr.get("name")}',
                                   "raw": fr, "lan_firewall": True, "network_rule": nr})
    e["sites"] = {}
    for kind in ("socket", "vsocket", "ipsec"):
        for s in (_load(os.path.join(config_dir, f"{kind}_sites.json"), {}) or {}).get("sites") or []:
            e["sites"][s["name"]] = dict(s, _kind=kind)
    e["global_ip_ranges"] = _load(os.path.join(config_dir, "global_ip_ranges.json"), []) or []
    e["allocated_ips"] = _load(os.path.join(config_dir, "allocated_ips.json"), []) or []
    e["static_hosts"] = _load(os.path.join(config_dir, "static_hosts.json"), []) or []
    e["webhooks"] = _load(os.path.join(config_dir, "webhooks.json"), []) or []
    e["groups"] = _load(os.path.join(config_dir, "groups.json"), []) or []
    e["custom_apps"] = _load(os.path.join(config_dir, "custom_applications.json"), []) or []
    return e


def collect_refs(rules):
    """kind -> name -> sorted list of 'family: rule' that reference it."""
    refs = defaultdict(lambda: defaultdict(set))

    def walk(o, label):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in REF_KINDS:
                    for x in (v if isinstance(v, list) else [v]):
                        if isinstance(x, dict) and x.get("name"):
                            refs[k][x["name"]].add(label)
                walk(v, label)
        elif isinstance(o, list):
            for x in o:
                walk(x, label)

    for r in rules:
        walk(r["raw"], f'{r["family"]}: {r["name"]}')
    return {k: {n: sorted(v) for n, v in d.items()} for k, d in refs.items()}


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.findings = []

    def add(self, category, severity, key, ctx, title, detail="", items=None, fix="", rules=None):
        """key: stable finding id the customer report phrases; ctx: the numbers/names it needs."""
        self.findings.append({"category": category, "severity": severity, "key": key, "ctx": ctx or {}, "title": title,
                              "detail": detail, "items": items or [], "fix": fix,
                              "rules": sorted(set(rules or []))})

    def counts(self):
        c = Counter(f["severity"] for f in self.findings)
        return {s: c.get(s, 0) for s in SEVERITIES}


def _rules_for(refs, kind, names):
    out = set()
    for n in names:
        out.update(refs.get(kind, {}).get(n, []))
    return out


def _uses(refs, kind, name):
    rs = refs.get(kind, {}).get(name, [])
    return f"{len(rs)} rule(s)" + (f", e.g. {rs[0]}" if rs else "")


# --------------------------------------------------------------------------
# Probes (opt-in): verify non-listable objects inside a discarded draft revision
# --------------------------------------------------------------------------
def _ref(n):
    return {"by": "NAME", "input": n}


def _ifw_rule(name):
    return {
        "enabled": False, "name": name, "description": "preflight probe (discarded)",
        "source": {k: [] for k in ["ip", "host", "site", "subnet", "ipRange", "globalIpRange", "networkInterface",
                                   "siteNetworkSubnet", "floatingSubnet", "user", "usersGroup", "group", "systemGroup"]},
        "country": [], "device": [], "deviceOS": [],
        "deviceAttributes": {k: [] for k in ["category", "type", "model", "manufacturer", "os", "osVersion"]},
        "destination": dict({k: [] for k in ["application", "customApp", "appCategory", "sanctionedAppsCategory", "country",
                                             "domain", "fqdn", "ip", "subnet", "ipRange", "globalIpRange", "remoteAsn", "group"]},
                            containers={"fqdnContainer": [], "ipAddressRangeContainer": []}),
        "service": {"standard": [], "custom": []}, "action": "BLOCK",
        "tracking": {"event": {"enabled": False}, "alert": {"enabled": False, "frequency": "DAILY",
                                                             "subscriptionGroup": [], "webhook": [], "mailingList": []}},
        "schedule": {"activeOn": "ALWAYS"}, "activePeriod": {"useEffectiveFrom": False, "useExpiresAt": False},
        "exceptions": [], "actionConfig": {"userNotification": [], "rbiProfile": []}, "connectionsOriginList": [],
    }


def _wnw_rule(name, priority):
    net = {k: [] for k in ["user", "floatingSubnet", "globalIpRange", "group", "host", "ip", "ipRange",
                           "networkInterface", "subnet", "site", "siteNetworkSubnet", "systemGroup", "usersGroup"]}
    return {"enabled": False, "name": name, "description": "preflight probe (discarded)", "ruleType": "INTERNET",
            "routeType": "NONE", "source": net, "destination": copy.deepcopy(net),
            "application": {k: [] for k in ["application", "appCategory", "customApp", "customServiceIp", "customService",
                                            "domain", "fqdn", "service", "globalIpRange", "group"]},
            "configuration": {"activeTcpAcceleration": False, "packetLossMitigation": False, "preserveSourcePort": False,
                              "primaryTransport": {"transportType": "AUTOMATIC", "primaryInterfaceRole": "AUTOMATIC",
                                                   "secondaryInterfaceRole": "AUTOMATIC"},
                              "secondaryTransport": {"transportType": "NONE", "primaryInterfaceRole": "NONE",
                                                     "secondaryInterfaceRole": "NONE"},
                              "allocationIp": [], "popLocation": [], "backhaulingSite": []},
            "bandwidthPriority": _ref(priority), "exceptions": []}


def _apc_data_rule(name, content=(), edm=()):
    crit = {"attributes": {
        "complianceAttributes": {k: "ANY" for k in ["hippa", "isae3402", "iso27001", "pciDss", "soc1", "soc2", "soc3", "sox"]},
        "securityAttributes": {k: "ANY" for k in ["auditTrail", "encryptionAtRest", "httpSecurityHeaders", "mfa", "rbac",
                                                  "rememberPassword", "sso", "tlsEnforcement", "trustedCertificate"]}},
        "originCountry": [], "risk": []}
    body = {"application": {"application": _ref("Dropbox"), "applicationType": []}, "applicationActivitySatisfy": "ANY",
            "applicationActivity": [{"activity": {"by": "ID", "input": "content_transfer_action_upload"}, "operator": "IS"}],
            "applicationContext": {"applicationTenant": []}, "accessMethod": [],
            "source": {k: [] for k in ["country", "host", "site", "subnet", "ip", "ipRange", "globalIpRange", "networkInterface",
                                       "siteNetworkSubnet", "floatingSubnet", "user", "usersGroup", "group", "systemGroup"]},
            "device": [], "action": "BLOCK",
            "tracking": {"event": {"enabled": False}, "alert": {"enabled": False, "frequency": "DAILY",
                                                                 "subscriptionGroup": [], "webhook": [], "mailingList": []}},
            "schedule": {"activeOn": "ALWAYS"}, "severity": "LOW", "applicationCriteriaSatisfy": "ANY",
            "applicationCriteria": crit, "fileAttributeSatisfy": "ANY", "fileAttribute": [],
            "dlpProfile": {"contentProfile": [_ref(n) for n in content], "edmProfile": [_ref(n) for n in edm]}}
    return {"enabled": False, "name": name, "description": "preflight probe (discarded)", "ruleType": "DATA", "dataRule": body}


class Prober:
    """Runs addRule probes inside one named draft revision per policy, then discards it."""

    ADD_TYPES = {"internetFirewall": "InternetFirewallAddRuleInput", "wanNetwork": "WanNetworkAddRuleInput",
                 "applicationControl": "ApplicationControlAddRuleInput", "siteWebProxy": "SiteWebProxyAddRuleInput"}

    def __init__(self, api, log):
        self.api, self.log, self.revisions = api, log, []

    def run(self, family, cases):
        """cases: list of (key, rule_dict). Returns key -> (status, [errors]) or {'_error': msg}."""
        api, out = self.api, {}
        rv = api.q('mutation r($a:ID!){ policy(accountId:$a){ %s{ createPolicyRevision(input:{name:"preflight-probe", '
                   'description:"preflight_check.py probe, discarded immediately"}){ status errors{errorCode errorMessage} '
                   'policy{ revision{ id } } } } } }' % family, allow_errors=True)
        try:
            rid = rv["data"]["policy"][family]["createPolicyRevision"]["policy"]["revision"]["id"]
        except (KeyError, TypeError):
            msg = (rv.get("errors") or [{}])[0].get("message") or json.dumps(rv)[:300]
            return {"_error": f"could not open a draft revision on {family}: {msg}"}
        self.revisions.append((family, rid))
        q_add = ('mutation a($a:ID!,$r:ID!,$i:%s!){ policy(accountId:$a){ %s(input:{revision:{id:$r}}){ '
                 'addRule(input:$i){ status errors{errorCode errorMessage} } } } }') % (self.ADD_TYPES[family], family)
        try:
            for key, rule in cases:
                r = api.q(q_add, {"r": rid, "i": {"rule": rule, "at": {"position": "LAST_IN_POLICY"}}}, allow_errors=True)
                if r.get("errors"):
                    out[key] = ("GQL_ERROR", [{"errorCode": "graphql", "errorMessage": r["errors"][0].get("message", ""),
                                               "path": r["errors"][0].get("path")}])
                else:
                    a = r["data"]["policy"][family]["addRule"]
                    out[key] = (a["status"], a.get("errors") or [])
        finally:
            d = api.q('mutation d($a:ID!,$r:ID!){ policy(accountId:$a){ %s(input:{revision:{id:$r}}){ '
                      'discardPolicyRevision(input:{id:$r}){ status } } } }' % family, {"r": rid}, allow_errors=True)
            ok = ((((d.get("data") or {}).get("policy") or {}).get(family) or {}).get("discardPolicyRevision") or {}).get("status")
            self.log(f"  probe revision on {family} discarded: {ok or d.get('errors')}")
        return out

    @staticmethod
    def entity_state(result, name):
        """MISSING / PRESENT / ERROR for a probe that referenced `name`."""
        status, errors = result
        if status == "SUCCESS":
            return "PRESENT", ""
        for e in errors:
            if e.get("errorCode") == "ruleHasInvalidEntity":
                return ("MISSING", e.get("errorMessage", "")) if name in e.get("errorMessage", "") else ("ERROR", e.get("errorMessage", ""))
        if status == "GQL_ERROR":
            return "ERROR", errors[0].get("errorMessage", "")
        return "PRESENT", "; ".join(e.get("errorMessage", "") for e in errors)  # entity resolved, rule failed later checks


def run_probes(api, refs, export, log):
    """Returns {'entities': {(kind, name): (state, msg)}, 'rbi': state, 'swp': state, 'revisions': [...]}"""
    prober, res = Prober(api, log), {"entities": {}, "features": {}}

    # Internet Firewall: custom categories, device posture, user notifications, RBI profiles, RBI action.
    cases, index = [("control", _ifw_rule("preflight-probe control"))], {}
    for kind, path in [("customCategory", ("destination", "customCategory")), ("device", ("device",)),
                       ("userNotification", ("actionConfig", "userNotification")), ("rbiProfile", ("actionConfig", "rbiProfile"))]:
        for n in sorted(refs.get(kind, {})):
            rule = _ifw_rule(f"preflight-probe {kind} {n}"[:250])
            tgt = rule
            for p in path[:-1]:
                tgt = tgt[p]
            tgt[path[-1]] = [_ref(n)]
            if kind == "rbiProfile":
                rule["action"] = "RBI"
                rule["destination"]["appCategory"] = [_ref("Uncategorized")]
            key = f"{kind}:{n}"
            cases.append((key, rule))
            index[key] = (kind, n)
    rbi_rules = [r for r in export["rules"] if r["raw"].get("action") == "RBI"]
    if rbi_rules:
        rule = _ifw_rule("preflight-probe RBI action")
        rule["action"] = "RBI"
        rule["destination"]["appCategory"] = [_ref("Uncategorized")]
        cases.append(("feature:rbi", rule))
    out = prober.run("internetFirewall", cases)
    if "_error" in out:
        res["error"] = out["_error"]
        return res
    for key, (kind, n) in index.items():
        res["entities"][(kind, n)] = Prober.entity_state(out[key], n)
    if "feature:rbi" in out:
        status, errs = out["feature:rbi"]
        msg = "; ".join(e.get("errorMessage", "") for e in errs)
        res["features"]["rbi"] = ("DISABLED", msg) if "RBI is disabled" in msg else (("ENABLED", "") if status == "SUCCESS" else ("ERROR", msg))
    if out.get("control", ("", []))[0] != "SUCCESS":
        res["warning"] = f"Internet Firewall control probe failed: {out.get('control')}"

    # WAN Network: bandwidth priorities beyond the defaults.
    prios = sorted(n for n in refs.get("bandwidthPriority", {}) if n not in DEFAULT_BANDWIDTH_PRIORITIES)
    for n in sorted(refs.get("bandwidthPriority", {})):
        if n in DEFAULT_BANDWIDTH_PRIORITIES:
            res["entities"][("bandwidthPriority", n)] = ("PRESENT", "built in")
    if prios:
        out = prober.run("wanNetwork", [(f"bw:{n}", _wnw_rule(f"preflight-probe bw {n}", n)) for n in prios])
        for n in prios:
            res["entities"][("bandwidthPriority", n)] = (Prober.entity_state(out[f"bw:{n}"], n)
                                                       if "_error" not in out else ("ERROR", out["_error"]))

    # App Control: DLP content and EDM profiles.
    dlp = [("contentProfile", n) for n in sorted(refs.get("contentProfile", {}))] + \
          [("edmProfile", n) for n in sorted(refs.get("edmProfile", {}))]
    if dlp:
        cases = [(f"{k}:{n}", _apc_data_rule(f"preflight-probe {k} {n}"[:250],
                                             content=[n] if k == "contentProfile" else [],
                                             edm=[n] if k == "edmProfile" else [])) for k, n in dlp]
        out = prober.run("applicationControl", cases)
        for k, n in dlp:
            res["entities"][(k, n)] = Prober.entity_state(out[f"{k}:{n}"], n) if "_error" not in out else ("ERROR", out["_error"])

    # Site Web Proxy: can a proxy rule be created at all?
    if any(r["family"] == "siteWebProxy" for r in export["rules"]):
        rule = {"enabled": False, "name": "preflight-probe swp", "description": "preflight probe (discarded)",
                "fqdn": "preflight-probe.invalid", "port": 8080, "associatedSite": [], "shouldAssociateWithAllSites": True,
                "authenticationConfig": {"method": "NONE"}}
        out = prober.run("siteWebProxy", [("swp", rule)])
        if "_error" in out:
            res["features"]["swp"] = ("ERROR", out["_error"])
        else:
            status, errs = out["swp"]
            msg = "; ".join(f'{e.get("errorMessage", "")} (path {e.get("path")})' if e.get("path") else e.get("errorMessage", "")
                            for e in errs)
            res["features"]["swp"] = ("ENABLED", "") if status == "SUCCESS" else ("FAILS", msg)
    res["revisions"] = prober.revisions
    return res


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_access(rep, src, tgt):
    cat = "access"
    sa, ta = src["account"], tgt["account"]
    rep.add(cat, "INFO", "access_accounts", {}, f"Source: {sa.get('name')} ({src['account_id']}, {sa.get('plan')}); "
                         f"target: {ta.get('name')} ({tgt['account_id']}, {ta.get('plan')})")
    if src["account_id"] == tgt["account_id"]:
        rep.add(cat, "BLOCKER", "access_same_account", {}, "Source and target are the same account", fix="Point --target-profile at the new tenant.")
    if (ta.get("plan") or "").upper() == "TRIAL":
        rep.add(cat, "WARN", "access_trial", {}, "Target is a TRIAL account: it reports no license or allocation data, so capacity "
                             "checks below can only tell you what to ask for, not whether it is there",
                fix="Confirm the numbers in CMA > Account > License (or with your Cato contact) before you start.")


def check_export(rep, exp, src):
    cat = "export"
    if exp["missing_files"]:
        rep.add(cat, "BLOCKER", "export_incomplete", {"n": len(exp["missing_files"])}, f"Source export in {exp['dir']} is incomplete", items=exp["missing_files"],
                fix="Re-run `catocli export all` (in two halves if it stops early, and check its summary: it can exit 0 "
                    "after a connection reset), then `catocli export static_hosts --site-type all` and "
                    "`scripts/export_webhooks.py`.")
    else:
        rep.add(cat, "OK", "export_ok", {"rules": len(exp["rules"]), "policies": len(exp["policy_enabled"])}, f"Source export in {exp['dir']} has every file the bulk folders read "
                           f"({len(exp['rules'])} rules across {len(exp['policy_enabled'])} policies)")
    n_export, n_live = len(exp["static_hosts"]), src.get("host_total")
    if n_live is not None and n_export < n_live:
        rep.add(cat, "BLOCKER", "export_hosts_short", {"have": n_export, "need": n_live}, f"Static hosts: export has {n_export}, the source tenant has {n_live}",
                detail="`export all` only exports hosts on physical sockets by default.",
                fix="`catocli export static_hosts --site-type all -f json --output-directory <config dir>`")
    elif n_live is not None:
        rep.add(cat, "OK", "export_hosts_ok", {"n": n_live}, f"Static hosts: all {n_live} exported")
    if not exp["rules"]:
        rep.add(cat, "WARN", "export_no_rules", {}, "No policy rules found in the export")


def check_licensing(rep, exp, src, tgt):
    cat = "licensing"
    need_ips = len(src["allocated_ips"]) or len(exp["allocated_ips"])
    t_pub = (tgt["allocations"] or {}).get("publicIps")
    already = len(tgt["allocated_ips"])
    if need_ips:
        if not t_pub:
            rep.add(cat, "ACTION", "lic_ips_unknown", {"need": need_ips}, f"Allocated (public) IPs: the source uses {need_ips}; the target reports no "
                                   f"public-IP license data ({already} already allocated there)",
                    fix=f"Make sure the target may allocate at least {need_ips} IPs before you deploy "
                        "0-dependencies. A fresh trial allowed only 3 in our test, and every allocation past the cap "
                        "fails with 'You cannot allocate more IPs than allowed by your license'.")
        elif (t_pub.get("available") or 0) + already < need_ips:
            rep.add(cat, "BLOCKER", "lic_ips_short", {"need": need_ips, "total": t_pub.get("total")}, f"Allocated (public) IPs: the source uses {need_ips}, the target has "
                                    f"{t_pub.get('total')} licensed ({t_pub.get('available')} free)",
                    fix=f"Raise the target's public IP license to at least {need_ips}.")
        else:
            rep.add(cat, "OK", "lic_ips_ok", {"need": need_ips}, f"Allocated (public) IPs: need {need_ips}, target has {t_pub.get('available')} free "
                               f"of {t_pub.get('total')}")
    s_z = (src["allocations"] or {}).get("ztnaUsers") or {}
    t_z = (tgt["allocations"] or {}).get("ztnaUsers")
    if s_z.get("allocated"):
        if not t_z:
            rep.add(cat, "ACTION", "lic_ztna_unknown", {"need": s_z["allocated"]}, f"ZTNA users: {s_z['allocated']} allocated in the source; the target reports no "
                                   "ZTNA user license data", fix=f"License at least {s_z['allocated']} ZTNA users in the target.")
        elif (t_z.get("total") or 0) < s_z["allocated"]:
            rep.add(cat, "BLOCKER", "lic_ztna_short", {"need": s_z["allocated"], "total": t_z.get("total")}, f"ZTNA users: source uses {s_z['allocated']}, target is licensed for {t_z.get('total')}",
                    fix="Raise the target's ZTNA user license.")
        else:
            rep.add(cat, "OK", "lic_ztna_ok", {"need": s_z["allocated"]}, f"ZTNA users: need {s_z['allocated']}, target licensed for {t_z.get('total')}")
    n_sites = len(exp["sites"])
    t_site_lic = [l for l in tgt["licenses"] if l.get("sku") in ("CATO_SITE", "CATO_SSE_SITE", "CATO_PB", "CATO_PB_SSE")]
    if n_sites:
        if not tgt["licenses"]:
            rep.add(cat, "ACTION", "lic_sites_unknown", {"need": n_sites}, f"Sites: the export holds {n_sites} sites; the target reports no site or pooled "
                                   "bandwidth licenses", fix=f"Make sure the target can hold {n_sites} sites "
                                                             "(site licenses or a pooled bandwidth license).")
        elif not t_site_lic:
            rep.add(cat, "BLOCKER", "lic_sites_none", {"need": n_sites}, f"Sites: {n_sites} to create, but the target has no site or pooled bandwidth license")
        else:
            rep.add(cat, "INFO", "lic_sites_info", {"need": n_sites}, f"Sites: {n_sites} to create; target has {len(t_site_lic)} site/pooled license record(s)")
    s_skus = {l["sku"] for l in src["licenses"] if l.get("status") in (None, "ACTIVE")}
    t_skus = {l["sku"] for l in tgt["licenses"] if l.get("status") in (None, "ACTIVE")}
    missing = sorted(s_skus - t_skus)
    if missing and t_skus:
        rep.add(cat, "WARN", "lic_skus_missing", {"n": len(missing)}, f"{len(missing)} license SKU(s) active in the source but not in the target",
                items=[f"{s} ({SKU_FEATURES.get(s, 'add-on')})" for s in missing],
                fix="Rules and settings that rely on these features may be rejected or behave differently.")
    elif missing:
        rep.add(cat, "ACTION", "lic_skus_unknown", {"n": len(missing)}, "Add-ons to match in the target (it reports no licenses to compare)",
                items=[f"{s} ({SKU_FEATURES.get(s, 'add-on')})" for s in missing])


def check_pops(rep, exp, src, tgt, refs):
    cat = "pops"
    needed = defaultdict(list)
    for ip in src["allocated_ips"]:
        needed[(ip.get("popLocation") or {}).get("name")].append(f"allocated IP {ip.get('ip')} ({ip.get('description') or ''})".strip())
    for n, rules in refs.get("popLocation", {}).items():
        needed[n].extend(rules)
    ip_by_name = {a.get("name"): (a.get("popLocation") or {}).get("name") for a in exp["allocated_ips"]}
    for s in exp["sites"].values():
        for side in ("primary", "secondary"):
            t = (s.get("ipsec") or {}).get(side) or {}
            pop = ip_by_name.get(t.get("public_cato_ip_name"))
            if pop:
                needed[pop].append(f"IPSec site {s['name']} ({side})")
    needed.pop(None, None)
    missing = {p: u for p, u in needed.items() if p not in tgt["locations"]}
    dep_rules = set()
    lost_ips = {a.get("name") for a in exp["allocated_ips"] if (a.get("popLocation") or {}).get("name") in missing}
    lost_sites = {s["name"] for s in exp["sites"].values()
                  if any(ip_by_name.get(((s.get("ipsec") or {}).get(side) or {}).get("public_cato_ip_name")) in missing
                         for side in ("primary", "secondary"))}
    for kind in ("allocationIp", "externalIp"):
        dep_rules |= _rules_for(refs, kind, lost_ips)
    for kind in ("site", "backhaulingSite", "associatedSite"):
        dep_rules |= _rules_for(refs, kind, lost_sites)
    for n, rules in refs.get("siteNetworkSubnet", {}).items():
        if n.split(" \\ ")[0] in lost_sites:
            dep_rules |= set(rules)
    for p in missing:
        dep_rules |= set(refs.get("popLocation", {}).get(p, []))
    if missing:
        rep.add(cat, "BLOCKER", "pop_missing", {"n": len(missing), "names": sorted(missing)}, f"{len(missing)} PoP location(s) the source uses are not available to the target",
                items=[f"{p}: used by {len(u)} item{'s' if len(u) != 1 else ''}, e.g. {u[0]}" for p, u in sorted(missing.items())],
                fix="China and private PoPs need their own entitlement. Get it added, or leave these allocations and "
                    "the sites/rules on them out with `allocated_ips_exclude` / `ipsec_sites_exclude` "
                    "(and scripts/scope_policies_to_target.py).", rules=dep_rules)
    rep.add(cat, "OK" if not missing else "INFO", "pop_summary", {"ok": len(needed) - len(missing), "n": len(needed)},
            f"{len(needed) - len(missing)} of {len(needed)} PoP locations used by the source are available to the target")


def _user_key(u):
    return (u.get("displayName") or f"{u.get('firstName', '')} {u.get('lastName', '')}").strip()


def check_identity(rep, exp, src, tgt, refs, target_api):
    cat = "identity"
    s_groups = {g["name"]: g for g in src["user_groups"]}
    t_groups = {g["name"]: g for g in tgt["user_groups"]}
    s_users = {}
    for u in src["users"]:
        s_users.setdefault(_user_key(u), u)
    t_user_names = {_user_key(u) for u in tgt["users"]}
    t_user_emails = {(u.get("email") or "").lower() for u in tgt["users"]}

    by_type, missing_rules, type_rules = defaultdict(list), set(), defaultdict(set)
    kinds = defaultdict(Counter)  # itype -> {"groups": n, "users": n}
    for name, rules in sorted(refs.get("usersGroup", {}).items()):
        if name in t_groups:
            continue
        itype = (s_groups.get(name) or {}).get("importType") or "NOT IN SOURCE"
        by_type[itype].append(f"user group '{name}' ({len(rules)} rule(s))")
        kinds[itype]["groups"] += 1
        missing_rules.update(rules)
        type_rules[itype].update(rules)
    for name, rules in sorted(refs.get("user", {}).items()):
        su = s_users.get(name)
        if name in t_user_names or (su and (su.get("email") or "").lower() in t_user_emails):
            continue
        itype = (su or {}).get("userImportType") or "NOT IN SOURCE"
        by_type[itype].append(f"user '{name}' ({len(rules)} rule(s))")
        kinds[itype]["users"] += 1
        missing_rules.update(rules)
        type_rules[itype].update(rules)
    fixes = {
        "SCIM": "Set up a SCIM app in the target (CMA > Access > Directory Services > SCIM) and provision from the IdP, "
                "or push from the source with `scripts/scim_sync.py export` then `push --target-profile <p>` "
                "(the target profile needs scim_url and scim_token). Groups must exist before the policies apply.",
        "LDAP": "Connect the target to the same directory (Directory Services > LDAP/AD) and sync these users/groups.",
        "MANUAL": "Create the user groups in the target CMA with the same names and members: the public API has no "
                  "mutation for user groups (`groups.createGroup` makes Advanced Groups, a different object). Manual "
                  "users can be created in the CMA or with `user.createUser` (Beta), which sets name, email and "
                  "department but not group membership. A SCIM push can also create the groups, but they then "
                  "belong to SCIM rather than MANUAL.",
        "DYNAMIC": "Turn on dynamic user groups (department) in the target, in the CMA or with "
                   "`user.updateDynamicUserGroupsConfiguration` (Beta), and make sure the attribute is populated.",
        "INTEGRATION": "Recreate the identity integration that provides these in the source.",
        "NOT IN SOURCE": "These names are in the export but not in the source tenant's user list: stale references. "
                         "Tidy the rules in the source, or leave them out of the run.",
    }
    for itype in sorted(by_type):
        rep.add(cat, "BLOCKER", "id_missing", {"type": itype, "n": len(by_type[itype]),
                                               "groups": kinds[itype]["groups"], "users": kinds[itype]["users"]}, f"{len(by_type[itype])} {itype} identities the rules name are missing in the target",
                items=by_type[itype], fix=fixes.get(itype, ""), rules=type_rules[itype])
    total_refs = len(refs.get("usersGroup", {})) + len(refs.get("user", {}))
    if total_refs and not by_type:
        rep.add(cat, "OK", "id_ok", {"n": total_refs}, f"All {total_refs} users and user groups the rules name exist in the target")
    if missing_rules:
        rep.add(cat, "INFO", "id_rules", {"n": len(missing_rules)}, f"{len(missing_rules)} rules name at least one missing identity and will fail until it exists")

    s_types = Counter(u.get("userImportType") for u in src["users"])
    t_types = Counter(u.get("userImportType") for u in tgt["users"])
    s_dirs = {u.get("directoryId") for u in src["users"] if u.get("directoryId")}
    rep.add(cat, "INFO", "id_source_dir", {}, f"Source directory: {len(src['users'])} users ({dict(s_types)}), "
                         f"{len(src['user_groups'])} user groups, {len(s_dirs)} directory id(s)")
    rep.add(cat, "INFO", "id_target_dir", {}, f"Target directory: {len(tgt['users'])} users ({dict(t_types)}), {len(tgt['user_groups'])} user groups")
    if s_types.get("SCIM") and not t_types.get("SCIM"):
        rep.add(cat, "ACTION", "id_scim_inactive", {}, "The source provisions users over SCIM; the target has no SCIM-provisioned users yet",
                detail="Either no SCIM integration exists in the target or it has not synced. The API cannot read the "
                       "IdP/SCIM configuration itself, so this is inferred from the user list.",
                fix="Create the SCIM app in the target, point the IdP (Entra ID, Okta, ...) at it, assign the groups "
                    "the rules use and run a sync before the policies.")
    if s_types.get("LDAP") and not t_types.get("LDAP"):
        rep.add(cat, "ACTION", "id_ldap_inactive", {}, "The source imports users from LDAP/AD; the target has none",
                fix="Connect the target to the directory and sync before the policies.")

    if target_api.scim_url and target_api._scim_token:
        try:
            st_u, body_u = target_api.scim_get("/Users?count=1")
            st_g, body_g = target_api.scim_get("/Groups?count=1")
            rep.add(cat, "OK", "id_scim_endpoint_ok", {}, f"Target SCIM endpoint answers (Users {st_u}: {body_u.get('totalResults')} total, "
                               f"Groups {st_g}: {body_g.get('totalResults')} total)")
        except Exception as e:  # noqa: BLE001 - report any failure as a finding
            rep.add(cat, "BLOCKER", "id_scim_endpoint_fail", {}, "Target SCIM endpoint in the profile does not answer", detail=str(e)[:300],
                    fix="Check scim_url and scim_token in the target profile (Directory Services > SCIM in the CMA).")
    else:
        rep.add(cat, "INFO", "id_scim_not_tested", {}, "No scim_url/scim_token in the target profile, so the SCIM endpoint was not tested",
                fix="Add both to the profile in ~/.cato/credentials to validate the SCIM app (and to use scim_sync.py).")


def check_cma_only(rep, refs, probes):
    cat = "cma-only"
    for kind, (one, many) in CMA_ONLY_KINDS.items():
        names = refs.get(kind, {})
        if kind == "bandwidthPriority":
            names = {n: r for n, r in names.items() if n not in DEFAULT_BANDWIDTH_PRIORITIES}
        if not names:
            continue
        noun = lambda k: one if k == 1 else many  # noqa: E731
        if probes and "entities" in probes:
            states = {n: probes["entities"].get((kind, n), ("UNKNOWN", "")) for n in names}
            missing = sorted(n for n, (st, _) in states.items() if st == "MISSING")
            unknown = sorted(n for n, (st, _) in states.items() if st not in ("MISSING", "PRESENT"))
            if missing:
                rep.add(cat, "BLOCKER", "cma_missing", {"kind": kind, "n": len(missing), "one": one, "many": many}, f"{len(missing)} {noun(len(missing))} missing in the target (verified by probe)",
                        items=[f"{n}: {_uses(refs, kind, n)}" for n in missing], rules=_rules_for(refs, kind, missing),
                        fix="Create them in the target CMA with exactly the same names. The API cannot create or list them.")
            if unknown:
                rep.add(cat, "WARN", "cma_undecided", {"kind": kind, "n": len(unknown), "one": one, "many": many}, f"{len(unknown)} {noun(len(unknown))} the probe could not decide",
                        items=[f"{n}: {states[n][1][:160]}" for n in unknown], rules=_rules_for(refs, kind, unknown))
            present = len(names) - len(missing) - len(unknown)
            if present:
                rep.add(cat, "OK", "cma_present", {"kind": kind, "n": present, "one": one, "many": many}, f"{present} {noun(present)} the rules use already exist in the target",
                        items=sorted(n for n, (st, _) in states.items() if st == "PRESENT"))
        else:
            rep.add(cat, "ACTION", "cma_unverified", {"kind": kind, "n": len(names), "one": one, "many": many}, f"{len(names)} {noun(len(names))} the rules use must exist in the target "
                                   "(the API cannot list them; rerun with --probe to verify)",
                    items=[f"{n}: {_uses(refs, kind, n)}" for n in sorted(names)], rules=_rules_for(refs, kind, names),
                    fix="Create them in the target CMA with exactly the same names before the policies.")


def check_features(rep, exp, refs, probes):
    cat = "features"
    rbi_rules = [f'{r["family"]}: {r["name"]}' for r in exp["rules"] if r["raw"].get("action") == "RBI"]
    if rbi_rules:
        state = ((probes or {}).get("features") or {}).get("rbi")
        if state and state[0] == "DISABLED":
            rep.add(cat, "BLOCKER", "feat_rbi_disabled", {"n": len(rbi_rules)}, "Remote Browser Isolation is disabled in the target (verified by probe)",
                    items=rbi_rules, rules=rbi_rules, fix="Enable RBI on the target (license/feature) before the Internet Firewall.")
        elif state and state[0] == "ENABLED":
            rep.add(cat, "OK", "feat_rbi_ok", {"n": len(rbi_rules)}, f"Remote Browser Isolation is enabled in the target ({len(rbi_rules)} rule(s) use it)")
        else:
            rep.add(cat, "ACTION", "feat_rbi_unverified", {"n": len(rbi_rules)}, f"{len(rbi_rules)} rule(s) use the RBI action: RBI must be enabled in the target",
                    items=rbi_rules, rules=rbi_rules, fix="Enable RBI, or rerun with --probe to verify.")
    swp_rules = [f'{r["family"]}: {r["name"]}' for r in exp["rules"] if r["family"] == "siteWebProxy"]
    if swp_rules:
        state = ((probes or {}).get("features") or {}).get("swp")
        if state and state[0] == "ENABLED":
            rep.add(cat, "OK", "feat_swp_ok", {}, "Site Web Proxy rules can be created in the target")
        elif state:
            rep.add(cat, "BLOCKER", "feat_swp_fail", {"n": len(swp_rules)}, "Site Web Proxy rules cannot be created in the target (verified by probe)",
                    detail=state[1][:300], items=swp_rules, rules=swp_rules,
                    fix="Ask Cato to enable Site Web Proxy for the target account. 'Unsupported field [addRule]' with "
                        "path ...rule.transportProtocol comes from the API itself, not from Terraform.")
        else:
            rep.add(cat, "ACTION", "feat_swp_unverified", {"n": len(swp_rules)}, f"{len(swp_rules)} Site Web Proxy rule(s): the feature must be enabled in the target",
                    items=swp_rules, rules=swp_rules, fix="Enable Site Web Proxy, or rerun with --probe to verify.")

    translated = []
    for s in exp["sites"].values():
        nr = s.get("native_range") or {}
        if nr.get("translated_subnet") and nr.get("subnet") and nr["translated_subnet"].strip() != nr["subnet"].strip():
            translated.append(f'{s["name"]}: native {nr["subnet"]} -> {nr["translated_subnet"]}')
        for lan in s.get("lan_interfaces") or []:
            for r in lan.get("network_ranges") or []:
                ts, sn = (r.get("translated_subnet") or "").strip(), (r.get("subnet") or "").strip()
                if ts and sn and ts != sn:
                    translated.append(f'{s["name"]} / {r.get("name")}: {sn} -> {ts}')
    if translated:
        rep.add(cat, "ACTION", "feat_srt", {"n": len(translated)}, f"{len(translated)} network range(s) use real Static Range Translation",
                items=translated, fix="Enable Static Range Translation for the target account in the CMA (Resources > "
                                      "System Settings) before the sites go in. The API reads and sets each range's "
                                      "translated subnet, and refuses one while the feature is off, but has no call "
                                      "for the account-level switch.")
    else:
        rep.add(cat, "OK", "feat_srt_ok", {}, "No real Static Range Translation in the source (identity translations are skipped "
                           "by the sites module)")

    l7, l7_rules = [], []
    for r in exp["rules"]:
        if not r.get("lan_firewall"):
            continue
        raw = r["raw"]
        app = raw.get("application") or {}
        svc = raw.get("service") or {}
        uses_l7 = any(app.get(k) for k in ("application", "customApp", "appCategory", "domain", "fqdn")) or svc.get("standard")
        if uses_l7:
            scope = r["network_rule"].get("site") or {}
            sites = sorted({x["name"] for x in scope.get("site") or []} | {f'group {x["name"]}' for x in scope.get("group") or []})
            l7.append(f'{r["name"]} (sites: {", ".join(sites) or "every site the network rule covers"})')
            l7_rules.append(f'{r["family"]}: {r["name"]}')
    if l7:
        rep.add(cat, "ACTION", "feat_l7", {"n": len(l7), "sites": sorted({x.rsplit("(sites: ", 1)[-1].rstrip(")") for x in l7})}, f"{len(l7)} LAN firewall rule(s) match on applications or services (Layer 7)",
                items=l7, rules=l7_rules,
                fix="Turn on LAN Layer 7 enforcement for those sites in the target CMA after the sites exist, or the "
                    "LAN firewall cannot publish ('Can't use L7 with sites that don't have L7 enforcement enabled'). "
                    "The API cannot set it.")

    waf = [g for g in exp["global_ip_ranges"] if "169.254.169.254" in json.dumps(g)]
    if waf:
        rep.add(cat, "BLOCKER", "feat_waf_range", {"n": len(waf), "names": [g.get("name") for g in waf]}, "Global IP range(s) containing 169.254.169.254: the API's web firewall rejects any "
                                "request that carries this address", items=[g.get("name") for g in waf],
                fix="Create them by hand in the target CMA, then keep their names in `global_ip_ranges_exclude` "
                    "(0-dependencies manages ALL global ranges and would otherwise try to recreate them).")

    wifi = []
    for s in exp["sites"].values():
        for w in s.get("wifi_ssids") or []:
            if ((w.get("security") or {}).get("mode") or "OPEN") != "OPEN":
                wifi.append(f'{s["name"]}:{w.get("name")}')
    if wifi:
        rep.add(cat, "ACTION", "feat_wifi", {"n": len(wifi)}, f"{len(wifi)} Wi-Fi SSID(s) need their passwords (exports carry CHANGE_ME)",
                items=wifi, fix="Put them in `wifi_ssid_passkeys` in a gitignored 1-sites/*.auto.tfvars.")
    if exp["webhooks"]:
        rep.add(cat, "WARN", "feat_webhooks", {"n": len(exp["webhooks"])}, f"{len(exp['webhooks'])} webhook(s) copy across pointing at the source's real endpoints",
                items=[f'{w.get("name")}: {w.get("url", "")[:60]}' for w in exp["webhooks"]],
                fix="In a test tenant, disable or repoint them; set real secrets via `webhook_secrets`.")
    ipsec = [s["name"] for s in exp["sites"].values() if s["_kind"] == "ipsec"]
    if ipsec:
        rep.add(cat, "INFO", "feat_ipsec", {"n": len(ipsec)}, f"{len(ipsec)} IPSec site(s): tunnels and pre-shared keys are not exported", items=ipsec,
                fix="Add tunnels and PSKs after the sites exist.")


def check_integrity(rep, exp, src, refs):
    cat = "integrity"
    classic_all = {n: r for n, r in refs.get("group", {}).items() if n not in src["groups"]}
    converted = {g.get("name") for g in exp["groups"]}
    done = sorted(n for n in classic_all if n in converted)
    classic = {n: r for n, r in classic_all.items() if n not in converted}
    if done:
        rep.add(cat, "OK", "int_classic_done", {"n": len(done)}, f"{len(done)} classic group(s) the API cannot see are already in groups.json as Advanced Groups",
                items=done)
    if classic:
        rep.add(cat, "ACTION", "int_classic", {"n": len(classic)}, f"{len(classic)} group(s) the rules use are invisible to the groups API (classic groups)",
                items=[f"{n}: {len(r)} rule(s)" for n, r in sorted(classic.items())], rules=_rules_for(refs, "group", classic),
                fix="Read their members in the source CMA and add them to config_data/groups.json as Advanced Groups "
                    "(keep empty ones empty: an empty group matches nothing; dropping it from a rule matches everything).")
    site_names = set(exp["sites"])
    stale = defaultdict(set)
    for kind in ("site", "backhaulingSite", "associatedSite"):
        for n, rules in refs.get(kind, {}).items():
            if n not in site_names:
                stale[f"site '{n}'"].update(rules)
    for n, rules in refs.get("siteNetworkSubnet", {}).items():
        site = n.split(" \\ ")[0]
        if site not in site_names:
            stale[f"subnet '{n}'"].update(rules)
    ga = {g.get("name") for g in exp["global_ip_ranges"]}
    for n, rules in refs.get("globalIpRange", {}).items():
        if n not in ga:
            stale[f"global IP range '{n}'"].update(rules)
    ca = {c.get("name") for c in exp["custom_apps"]} if isinstance(exp["custom_apps"], list) else set()
    for n, rules in refs.get("customApp", {}).items():
        if ca and n not in ca:
            stale[f"custom app '{n}'"].update(rules)
    an = {a.get("name") for a in exp["allocated_ips"]}
    for kind in ("allocationIp", "externalIp"):
        for n, rules in refs.get(kind, {}).items():
            if n not in an:
                stale[f"allocated IP '{n}'"].update(rules)
    if stale:
        rep.add(cat, "WARN", "int_stale", {"n": len(stale)}, f"{len(stale)} reference(s) point at objects that are not in the source export",
                items=[f"{k}: {len(v)} rule(s), e.g. {sorted(v)[0]}" for k, v in sorted(stale.items())],
                rules=set().union(*stale.values()),
                fix="Usually leftovers in the source (deleted sites/ranges). Tidy them there, or leave the rules out.")
    else:
        rep.add(cat, "OK", "int_ok", {}, "Every site, range, custom app and allocated IP the rules name is in the export")


def check_target_state(rep, exp, tgt):
    cat = "target"
    src_names = defaultdict(set)
    for r in exp["rules"]:
        if not r.get("lan_firewall"):
            src_names[r["family"]].add(r["name"])
    clashes, existing = [], []
    for fam, info in tgt["policies"].items():
        if "error" in info:
            rep.add(cat, "WARN", "tgt_unreadable", {"family": fam}, f"Could not read the target's {fam} policy", detail=info["error"][:200])
            continue
        names = set(info["rules"])
        if names:
            existing.append(f"{fam}: {len(names)} rule(s)")
        both = sorted(names & src_names.get(fam, set()))
        if both:
            clashes.append(f"{fam}: {len(both)} ({', '.join(both[:3])}{', ...' if len(both) > 3 else ''})")
    if clashes:
        rep.add(cat, "ACTION", "tgt_clash", {"n": sum(int(c.split(": ", 1)[1].split(" ")[0]) for c in clashes)}, "Rules already in the target share names with source rules "
                               "(factory/sample rules, or a previous run)",
                items=clashes, fix="New tenant: review, then clear them with `scripts/purge_all.py --profile <target> "
                                   "--only <families>` (dry run by default; --execute deletes). Re-run of an earlier "
                                   "migration: leave them, Terraform state already owns them.")
    if existing and not clashes:
        rep.add(cat, "WARN", "tgt_rules_existing", {}, "The target already has rules (no name clashes)", items=existing)
    elif existing:
        rep.add(cat, "INFO", "tgt_rules_info", {}, "Rules already in the target", items=existing)
    for label, have in (("sites", tgt["sites"]), ("groups", tgt["groups"])):
        both = sorted(have & (set(exp["sites"]) if label == "sites" else {g.get("name") for g in exp["groups"]}))
        if both:
            rep.add(cat, "WARN", "tgt_existing_named", {"label": label, "n": len(both)}, f"{len(both)} {label} with source names already exist in the target",
                    items=both, fix="A greenfield run expects them absent: import them (brownfield) or remove them first.")
        elif have:
            rep.add(cat, "INFO", "tgt_existing_info", {"label": label, "n": len(have)}, f"Target already has {len(have)} {label} (no name clashes)")
    if tgt["allocated_ips"]:
        rep.add(cat, "INFO", "tgt_allocated", {"n": len(tgt["allocated_ips"])}, f"Target already has {len(tgt['allocated_ips'])} allocated IP(s); they count against the cap")
    diffs = [f"{fam}: source {'on' if on else 'off'}, target {'on' if tgt['policies'][fam].get('enabled') else 'off'}"
             for fam, on in sorted(exp["policy_enabled"].items())
             if fam in tgt["policies"] and "error" not in tgt["policies"][fam] and tgt["policies"][fam].get("enabled") != on]
    if diffs:
        rep.add(cat, "INFO", "tgt_toggles", {"n": len(diffs)}, f"{len(diffs)} policy on/off switch(es) differ today (the policy folders set them to match)",
                items=diffs)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
CATEGORY_TITLES = [
    ("access", "Access and accounts"), ("export", "Source export"), ("licensing", "Licensing and capacity"),
    ("pops", "PoP locations"), ("identity", "Users, groups, SCIM and IdP"), ("cma-only", "Objects only the CMA can create"),
    ("features", "Features and account settings"), ("integrity", "Source integrity"), ("target", "Target state"),
]


def blocked_rule_count(rep, severities=("BLOCKER",)):
    return len({r for f in rep.findings if f["severity"] in severities for r in f["rules"]})


def rule_totals(exp):
    nested = sum(1 for r in exp["rules"] if r.get("lan_firewall"))
    return len(exp["rules"]), nested


def render_text(rep, exp, meta):
    lines = [f"Pre-migration dependency check: {meta['source']} -> {meta['target']}"
             f"{'  (with probes)' if meta['probe'] else ''}", ""]
    for cat, title in CATEGORY_TITLES:
        fs = sorted([f for f in rep.findings if f["category"] == cat], key=lambda f: SEVERITIES.index(f["severity"]))
        if not fs:
            continue
        lines.append(f"== {title}")
        for f in fs:
            lines.append(f"  {SEV_ICON[f['severity']]:5s} {f['severity']:7s} {f['title']}")
            if f["detail"]:
                lines.append(f"                {f['detail']}")
            for it in f["items"][:12]:
                lines.append(f"                - {it}")
            if len(f["items"]) > 12:
                lines.append(f"                - ... and {len(f['items']) - 12} more (see the report)")
            if f["fix"] and f["severity"] in ("BLOCKER", "ACTION", "WARN"):
                lines.append(f"                fix: {f['fix']}")
        lines.append("")
    c = rep.counts()
    lines.append("Summary: " + ", ".join(f"{c[s]} {s}" for s in SEVERITIES))
    total, nested = rule_totals(exp)
    lines.append(f"Rules that will fail until the BLOCKERs are fixed: {blocked_rule_count(rep)} of {total} "
                 f"({nested} of them nested LAN firewall rules)")
    lines.append(f"Rules that also depend on an ACTION item: {blocked_rule_count(rep, ('BLOCKER', 'ACTION'))} of {total}")
    return "\n".join(lines)


def render_markdown(rep, exp, meta):
    c = rep.counts()
    out = [f"# Pre-migration dependency check", "",
           f"Source **{meta['source']}**, target **{meta['target']}**, run {meta['when']}"
           f"{' with probes' if meta['probe'] else ' (read-only)'}.", "",
           "| Blockers | Actions | Warnings | Info | OK | Rules blocked | Rules blocked or at risk |",
           "|---:|---:|---:|---:|---:|---:|---:|",
           f"| {c['BLOCKER']} | {c['ACTION']} | {c['WARN']} | {c['INFO']} | {c['OK']} | "
           f"{blocked_rule_count(rep)} of {rule_totals(exp)[0]} | {blocked_rule_count(rep, ('BLOCKER', 'ACTION'))} of {rule_totals(exp)[0]} |", "",
           "BLOCKER: the migration will fail until this is done. ACTION: do this before you migrate (or confirm it "
           "is done). WARN: review. INFO and OK: for the record.", ""]
    for cat, title in CATEGORY_TITLES:
        fs = sorted([f for f in rep.findings if f["category"] == cat], key=lambda f: SEVERITIES.index(f["severity"]))
        if not fs:
            continue
        out += [f"## {title}", ""]
        for f in fs:
            out.append(f"**{f['severity']}**: {f['title']}")
            if f["detail"]:
                out.append(f"\n{f['detail']}")
            if f["items"]:
                out.append("")
                out += [f"- {it}" for it in f["items"]]
            if f["fix"] and f["severity"] != "OK":
                out.append(f"\n*What to do:* {f['fix']}")
            out.append("")
    if meta.get("revisions"):
        out += ["## Probe revisions", "", "Created and discarded on the target during this run:", ""]
        out += [f"- {fam}: {rid}" for fam, rid in meta["revisions"]]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--source-profile", required=True, help="catocli profile of the SOURCE tenant (read-only is enough)")
    ap.add_argument("--target-profile", required=True, help="catocli profile of the TARGET tenant")
    ap.add_argument("--config-dir", default=os.path.join(REPO_ROOT, "config_data"),
                    help="the source export (default: ./config_data)")
    ap.add_argument("--probe", action="store_true",
                    help="verify non-listable objects/features in a discarded draft revision on the target (needs edit rights)")
    ap.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "preflight"), help="where to write the reports")
    ap.add_argument("--customer", default=None,
                    help="customer name for the cover of the branded report (default: the target account name)")
    ap.add_argument("--pdf", action="store_true",
                    help="also print the branded report to preflight_report.pdf (needs a local Chrome, Chromium or Edge)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when ACTION items remain (CI use)")
    ap.add_argument("--quiet", action="store_true", help="print only the summary")
    args = ap.parse_args()

    log = (lambda m: None) if args.quiet else (lambda m: print(m, file=sys.stderr))
    src_api, tgt_api = Api(args.source_profile), Api(args.target_profile)
    if args.probe:
        protected_accounts, protected_profiles = load_protected()
        if (tgt_api.account in protected_accounts or args.target_profile in protected_profiles
                or tgt_api.account == src_api.account):
            print(f"REFUSED: --probe against account {tgt_api.account}, which is a protected source "
                  "(or the source itself).", file=sys.stderr)
            return 2

    log(f"reading source export from {args.config_dir}")
    exp = load_export(args.config_dir)
    log(f"reading source tenant {src_api.account} ({args.source_profile})")
    src = load_tenant(src_api, with_policies=False)
    log(f"reading target tenant {tgt_api.account} ({args.target_profile})")
    tgt = load_tenant(tgt_api)
    refs = collect_refs(exp["rules"])

    probes = None
    if args.probe:
        log("probing the target inside discarded draft revisions")
        probes = run_probes(tgt_api, refs, exp, log)

    rep = Report()
    check_access(rep, src, tgt)
    check_export(rep, exp, src)
    check_licensing(rep, exp, src, tgt)
    check_pops(rep, exp, src, tgt, refs)
    check_identity(rep, exp, src, tgt, refs, tgt_api)
    check_cma_only(rep, refs, probes)
    check_features(rep, exp, refs, probes)
    check_integrity(rep, exp, src, refs)
    check_target_state(rep, exp, tgt)
    if probes and probes.get("error"):
        rep.add("access", "WARN", "probe_failed", {}, "Probes could not run", detail=probes["error"][:300],
                fix="--probe needs a target key with edit rights on the policies.")
    if probes and probes.get("warning"):
        rep.add("access", "WARN", "probe_baseline", {}, "A probe baseline failed, so probe results may be unreliable", detail=probes["warning"][:300])

    meta = {"source": f"{src['account'].get('name')} ({src_api.account})",
            "target_name": tgt["account"].get("name"),
            "target": f"{tgt['account'].get('name')} ({tgt_api.account})", "probe": args.probe,
            "when": time.strftime("%Y-%m-%d %H:%M %Z"), "revisions": (probes or {}).get("revisions", [])}
    text = render_text(rep, exp, meta)
    if args.quiet:
        print("\n".join(text.splitlines()[-2:]))
    else:
        print(text)
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "preflight_report.md"), "w") as fh:
        fh.write(render_markdown(rep, exp, meta))
    with open(os.path.join(args.out_dir, "preflight_report.json"), "w") as fh:
        json.dump({"meta": meta, "counts": rep.counts(), "rules_total": len(exp["rules"]),
                   "rules_blocked": blocked_rule_count(rep),
                   "rules_blocked_or_at_risk": blocked_rule_count(rep, ("BLOCKER", "ACTION")), "findings": rep.findings}, fh, indent=1, default=list)
    html_path = os.path.join(args.out_dir, "preflight_report.html")
    with open(html_path, "w") as fh:
        fh.write(render_customer_html(rep, exp, meta, customer=args.customer))
    written = ["preflight_report.md", "preflight_report.json", "preflight_report.html"]
    if args.pdf:
        err = html_to_pdf(html_path, os.path.join(args.out_dir, "preflight_report.pdf"))
        if err:
            log(f"PDF not written: {err}")
        else:
            written.append("preflight_report.pdf")
    log(f"reports written to {args.out_dir}: {', '.join(written)}")
    c = rep.counts()
    if c["BLOCKER"]:
        return 2
    if args.strict and c["ACTION"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
