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
        self.assertTrue(any(f["severity"] == "ACTION" and "SCIM" in f["title"] for f in rep.findings))

    def test_classic_groups_already_converted_are_ok(self):
        rules = [rule("internetFirewall", "R1", source={"group": [{"name": "Legacy"}, {"name": "Other"}]})]
        exp = export(rules=rules, groups=[{"name": "Legacy"}])
        rep = pc.Report()
        pc.check_integrity(rep, exp, tenant(groups=set()), pc.collect_refs(rules))
        sev = sorted(f["severity"] for f in rep.findings if "classic" in f["title"])
        self.assertEqual(sev, ["ACTION", "OK"])


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


if __name__ == "__main__":
    unittest.main()
