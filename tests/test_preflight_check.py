"""Offline tests for scripts/preflight_check.py (no API calls).

Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import preflight_check as pc  # noqa: E402


def rule(family, name, **raw):
    return {"family": family, "name": name, "raw": dict(raw, name=name)}


def export(rules=(), sites=None, allocated=(), ranges=(), groups=(), webhooks=()):
    return {"dir": "x", "rules": list(rules), "policy_enabled": {}, "missing_files": [], "sites": sites or {},
            "global_ip_ranges": list(ranges), "allocated_ips": list(allocated), "static_hosts": [],
            "webhooks": list(webhooks), "groups": list(groups), "custom_apps": []}


def tenant(**kw):
    t = {"account_id": "1", "account": {"name": "t", "plan": "COMMERCIAL"}, "licenses": [], "allocations": {},
         "allocated_ips": [], "locations": set(), "sites": set(), "host_total": 0, "users": [], "user_groups": [],
         "groups": set(), "policies": {}}
    t.update(kw)
    return t


class FakeApi:
    scim_url = None
    _scim_token = None


class CollectRefs(unittest.TestCase):
    def test_nested_refs_are_labelled_by_rule(self):
        r = rule("internetFirewall", "R1", source={"usersGroup": [{"id": "1", "name": "Admins"}]},
                 destination={"customCategory": [{"name": "Pen Testing"}]}, device=[{"name": "Mac posture"}])
        refs = pc.collect_refs([r])
        self.assertEqual(refs["usersGroup"]["Admins"], ["internetFirewall: R1"])
        self.assertIn("Pen Testing", refs["customCategory"])
        self.assertIn("Mac posture", refs["device"])

    def test_single_object_refs(self):
        r = rule("wanNetwork", "W1", bandwidthPriority={"name": "50"})
        self.assertEqual(pc.collect_refs([r])["bandwidthPriority"], {"50": ["wanNetwork: W1"]})


class EntityState(unittest.TestCase):
    def test_success_is_present(self):
        self.assertEqual(pc.Prober.entity_state(("SUCCESS", []), "X")[0], "PRESENT")

    def test_invalid_entity_naming_the_object_is_missing(self):
        err = [{"errorCode": "ruleHasInvalidEntity", "errorMessage": "Rule has an invalid entity: CustomCategory(name: X)"}]
        self.assertEqual(pc.Prober.entity_state(("FAILURE", err), "X")[0], "MISSING")

    def test_invalid_entity_for_something_else_is_error(self):
        err = [{"errorCode": "ruleHasInvalidEntity", "errorMessage": "Rule has an invalid entity: Application(name: Y)"}]
        self.assertEqual(pc.Prober.entity_state(("FAILURE", err), "X")[0], "ERROR")

    def test_other_validation_errors_mean_the_entity_resolved(self):
        err = [{"errorCode": "applicationControl-400", "errorMessage": "action: MONITOR action is not allowed"}]
        self.assertEqual(pc.Prober.entity_state(("FAILURE", err), "X")[0], "PRESENT")


class Checks(unittest.TestCase):
    def test_cma_only_without_probe_is_action_and_with_probe_is_blocker(self):
        refs = pc.collect_refs([rule("wanNetwork", "W1", bandwidthPriority={"name": "50"}),
                                rule("wanNetwork", "W2", bandwidthPriority={"name": "10"})])
        rep = pc.Report()
        pc.check_cma_only(rep, refs, None)
        self.assertEqual([f["severity"] for f in rep.findings], ["ACTION"])  # 10 is a default priority
        rep = pc.Report()
        pc.check_cma_only(rep, refs, {"entities": {("bandwidthPriority", "50"): ("MISSING", "")}})
        self.assertEqual(rep.findings[0]["severity"], "BLOCKER")
        self.assertEqual(rep.findings[0]["rules"], ["wanNetwork: W1"])

    def test_link_local_range_and_real_translation(self):
        sites = {"S1": {"name": "S1", "_kind": "socket", "native_range": {"subnet": "10.0.0.0/24", "translated_subnet": "10.9.0.0/24"},
                        "lan_interfaces": [], "wifi_ssids": []}}
        exp = export(sites=sites, ranges=[{"name": "Meta", "ipRange": "169.254.169.254/32"}])
        rep = pc.Report()
        pc.check_features(rep, exp, {}, None)
        sev = {f["title"].split(" ")[0]: f["severity"] for f in rep.findings}
        self.assertEqual(sev.get("Global"), "BLOCKER")
        self.assertTrue(any(f["severity"] == "ACTION" and "Static Range Translation" in f["title"] for f in rep.findings))

    def test_lan_layer7_names_the_sites(self):
        nr = {"name": "NR", "site": {"site": [{"name": "Branch-1"}], "group": []}}
        fw = {"family": "socketLan", "name": "NR :: FW", "raw": {"name": "FW", "application": {"customApp": [{"name": "App"}]}},
              "lan_firewall": True, "network_rule": nr}
        rep = pc.Report()
        pc.check_features(rep, export(rules=[fw]), {}, None)
        l7 = [f for f in rep.findings if "Layer 7" in f["title"]][0]
        self.assertIn("Branch-1", l7["items"][0])
        self.assertEqual(l7["rules"], ["socketLan: NR :: FW"])

    def test_missing_pop_blocks_dependant_rules(self):
        exp = export(allocated=[{"name": "Shanghai_DC2 - 1.2.3.4", "popLocation": {"name": "Shanghai_DC2"}}],
                     rules=[rule("wanNetwork", "Egress", configuration={"allocationIp": [{"name": "Shanghai_DC2 - 1.2.3.4"}]})])
        src = tenant(allocated_ips=[{"ip": "1.2.3.4", "popLocation": {"name": "Shanghai_DC2"}}])
        tgt = tenant(locations={"Ashburn"})
        rep = pc.Report()
        pc.check_pops(rep, exp, src, tgt, pc.collect_refs(exp["rules"]))
        blocker = [f for f in rep.findings if f["severity"] == "BLOCKER"][0]
        self.assertEqual(blocker["rules"], ["wanNetwork: Egress"])

    def test_identity_split_by_import_type(self):
        rules = [rule("internetFirewall", "R1", source={"usersGroup": [{"name": "Admins"}, {"name": "Remote"}]})]
        src = tenant(user_groups=[{"name": "Admins", "importType": "MANUAL"}, {"name": "Remote", "importType": "SCIM"}],
                     users=[{"displayName": "A", "userImportType": "SCIM"}])
        tgt = tenant(user_groups=[{"name": "Remote", "importType": "SCIM"}])
        rep = pc.Report()
        pc.check_identity(rep, export(rules=rules), src, tgt, pc.collect_refs(rules), FakeApi())
        blockers = [f for f in rep.findings if f["severity"] == "BLOCKER"]
        self.assertEqual(len(blockers), 1)
        self.assertIn("MANUAL", blockers[0]["title"])
        self.assertEqual((blockers[0]["ctx"]["groups"], blockers[0]["ctx"]["users"]), (1, 0))
        self.assertTrue(any(f["severity"] == "ACTION" and "SCIM" in f["title"] for f in rep.findings))

    def test_classic_groups_already_converted_are_ok(self):
        rules = [rule("internetFirewall", "R1", source={"group": [{"name": "Legacy"}, {"name": "Other"}]})]
        exp = export(rules=rules, groups=[{"name": "Legacy"}])
        rep = pc.Report()
        pc.check_integrity(rep, exp, tenant(groups=set()), pc.collect_refs(rules))
        sev = sorted(f["severity"] for f in rep.findings if "classic" in f["title"])
        self.assertEqual(sev, ["ACTION", "OK"])


def _read(path):
    with open(path) as fh:
        return fh.read()


class LeaveOut(unittest.TestCase):
    """The check writes the scope command and exclusion files for whatever is still missing."""

    def _report(self):
        refs = pc.collect_refs([rule("wanNetwork", "W1", bandwidthPriority={"name": "50"})])
        rep = pc.Report()
        pc.check_cma_only(rep, refs, {"entities": {("bandwidthPriority", "50"): ("MISSING", "")}})
        nr = {"name": "NR", "site": {"site": [{"name": "Branch-1"}], "group": []}}
        fw = {"family": "socketLan", "name": "NR :: FW", "raw": {"name": "FW", "application": {"customApp": [{"name": "A"}]}},
              "lan_firewall": True, "network_rule": nr}
        pc.check_features(rep, export(rules=[fw], ranges=[{"name": "AzureVDI-2", "ipRange": "169.254.169.254/32"}]), {}, None)
        rep.leave_out.add(("siteNetworkSubnet", "Site \\ LAN \\ It's a range"))  # backslashes and a quote
        return rep

    def test_findings_feed_the_leave_out_lists(self):
        rep = self._report()
        self.assertIn(("bandwidthPriority", "50"), rep.leave_out)
        self.assertIn(("globalIpRange", "AzureVDI-2"), rep.leave_out)
        self.assertEqual(rep.skip_rules, {"NR / FW"})  # nested LAN rules use the scope script's " / " form
        self.assertEqual(rep.exclude["global_ip_ranges_exclude"], {"AzureVDI-2"})

    def test_written_command_round_trips_every_name(self):
        import shlex
        import subprocess
        import tempfile
        rep = self._report()
        with tempfile.TemporaryDirectory() as d:
            written = pc.write_leave_out(rep, os.path.join(d, "config_data"), d)
            self.assertEqual(len(written), 3)
            cmd = _read(os.path.join(d, "scope_command.sh"))
            self.assertEqual(subprocess.run(["sh", "-n", os.path.join(d, "scope_command.sh")]).returncode, 0)
            argv = shlex.split(" ".join(l.rstrip("\\") for l in cmd.splitlines() if not l.startswith("#")))
            self.assertIn("siteNetworkSubnet=Site \\ LAN \\ It's a range", argv)
            self.assertIn("bandwidthPriority=50", argv)
            self.assertEqual(argv[argv.index("--skip-rule") + 1], "NR / FW")
            self.assertEqual(argv[argv.index("--out") + 1], os.path.join(d, "config_data_target"))
            tfv = _read(os.path.join(d, "exclude-0-dependencies.auto.tfvars"))
            self.assertIn('global_ip_ranges_exclude = ["AzureVDI-2"]', tfv)
            self.assertIn("allocated_ips_exclude = []", tfv)
            self.assertNotIn("wifi", _read(os.path.join(d, "exclude-1-sites.auto.tfvars")))

    def test_nothing_missing_still_writes_a_plain_copy_command(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            pc.write_leave_out(pc.Report(), os.path.join(d, "config_data"), d)
            cmd = [l for l in _read(os.path.join(d, "scope_command.sh")).splitlines() if not l.startswith("#")]
            self.assertEqual(len(cmd), 1)
            self.assertFalse(cmd[0].endswith("\\"))


class ScimEndpoint(unittest.TestCase):
    """The SCIM check against a local stand-in for the Cato SCIM app."""

    def _serve(self, status):
        import http.server
        import json as _json
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                ok = self.headers.get("Authorization") == "Bearer good-token" and status == 200
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/scim+json")
                self.end_headers()
                self.wfile.write(_json.dumps({"totalResults": 7 if ok else 0}).encode())

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/scim/v2/1/abc"

    def _run(self, url, token):
        api = pc.Api.__new__(pc.Api)
        api.scim_url, api._scim_token = url, token
        rep = pc.Report()
        pc.check_identity(rep, export(), tenant(), tenant(), {}, api)
        return [f for f in rep.findings if "SCIM endpoint" in f["title"]][0]

    def test_answering_endpoint_is_ok(self):
        f = self._run(self._serve(200), "good-token")
        self.assertEqual(f["severity"], "OK")
        self.assertIn("7 total", f["title"])

    def test_rejected_token_is_blocker(self):
        self.assertEqual(self._run(self._serve(200), "bad-token")["severity"], "BLOCKER")


class CustomerReport(unittest.TestCase):
    """The Cato-branded report written for the customer."""

    def setUp(self):
        import preflight_report as pr
        self.pr = pr

    def test_every_actionable_key_has_customer_wording(self):
        import re
        src = open(pc.__file__).read()
        keys = set(re.findall(r'rep\.add\((?:cat|"access"), "(?:BLOCKER|ACTION|WARN)", "([a-z0-9_]+)"', src))
        self.assertGreater(len(keys), 30)
        ctx = {"n": 2, "need": 19, "total": 3, "have": 15, "type": "SCIM", "names": ["Shanghai_DC2"], "kind": "device",
               "one": "device posture profile", "many": "device posture profiles", "sites": ["Branch-1"], "label": "sites",
               "family": "internetFirewall"}
        owners = {o[0] for o in self.pr.OWNERS}
        generic = {"probe_failed", "probe_baseline", "tgt_unreadable", "tgt_rules_existing"}
        for k in sorted(keys - generic):
            g = self.pr.guide({"key": k, "ctx": ctx, "rules": ["a: b"], "title": "ENGINEERING TITLE", "detail": ""})
            self.assertIn(g["owner"], owners, k)
            self.assertNotEqual(g["title"], "ENGINEERING TITLE", f"{k} has no customer wording")
            self.assertTrue(g["why"], k)

    def test_identity_owner_depends_on_import_type(self):
        f = {"key": "id_missing", "rules": [], "title": "", "detail": ""}
        self.assertEqual(self.pr.guide(dict(f, ctx={"type": "SCIM", "n": 3}))["owner"], "identity")
        self.assertEqual(self.pr.guide(dict(f, ctx={"type": "MANUAL", "n": 3}))["owner"], "cma_admin")

    def test_manual_users_and_groups_are_worded_apart(self):
        # The API can create users (user.createUser) but not user groups, so the wording must not lump them.
        f = {"key": "id_missing", "rules": [], "title": "", "detail": ""}
        groups = self.pr.guide(dict(f, ctx={"type": "MANUAL", "n": 2, "groups": 2, "users": 0}))
        self.assertEqual(groups["title"], "Create 2 user groups in the CMA")
        mixed = self.pr.guide(dict(f, ctx={"type": "MANUAL", "n": 3, "groups": 1, "users": 2}))
        self.assertEqual(mixed["title"], "Create 1 user group and 2 users")
        self.assertIn("through the Cato API", mixed["why"])
        users = self.pr.guide(dict(f, ctx={"type": "MANUAL", "n": 1, "groups": 0, "users": 1}))
        self.assertEqual(users["title"], "Create 1 user")
        self.assertNotIn("can't create user groups", users["why"])

    def test_friendly_text(self):
        fr = self.pr._friendly
        self.assertEqual(fr("internetFirewall: RBI Check Apps"), "Internet Firewall: RBI Check Apps")
        self.assertEqual(fr("user group 'X' (1 rule(s))"), "user group 'X' (1 rule)")
        self.assertEqual(fr("CATO_RBI (Remote Browser Isolation)", "lic_skus_unknown"), "Remote Browser Isolation (CATO_RBI)")

    def test_render_has_brand_and_plan(self):
        rep = pc.Report()
        rep.add("pops", "BLOCKER", "pop_missing", {"n": 1, "names": ["Shanghai_DC2"]}, "x",
                items=["Shanghai_DC2: used by 1 item"], rules=["wanNetwork: Egress"])
        rep.add("features", "ACTION", "feat_wifi", {"n": 1}, "y", items=["S1:Guest"])
        rep.add("export", "OK", "export_ok", {"rules": 2, "policies": 1}, "z")
        exp = export(rules=[rule("wanNetwork", "Egress"), rule("wanNetwork", "Other")])
        meta = {"source": "Src (1)", "target": "Tgt (2)", "target_name": "Tgt", "when": "2026-11-24", "probe": True}
        out = self.pr.render_customer_html(rep, exp, meta, customer="Example Co")
        for needle in ("Migration readiness report", "Prepared for", "Example Co", "Your action plan",
                       "Required before migration", "Already in place", "Appendix A", "@font-face",
                       'class="cato-logo"', "<b>WAN Network</b>", "<span>Egress</span>",
                       ">1</span>Make 1 PoP location available"):
            self.assertTrue(needle in out, f"missing from report: {needle}")
        self.assertTrue("50%" in out, "readiness gauge should read 50%")  # 1 of 2 rules ready

    def test_modified_logo_is_left_out(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "cato_logo_rgb_white_art_101.svg"), "w") as fh:
                fh.write("<svg/>")
            old, self.pr.ASSETS = self.pr.ASSETS, d
            try:
                self.assertEqual(self.pr._logo("cato_logo_rgb_white_art_101.svg"), "")
            finally:
                self.pr.ASSETS = old
        self.assertIn("data:image/svg+xml;base64", self.pr._logo("cato_logo_rgb_white_art_101.svg"))


if __name__ == "__main__":
    unittest.main()
