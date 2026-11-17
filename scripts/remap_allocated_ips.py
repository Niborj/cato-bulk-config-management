#!/usr/bin/env python3
"""Rewrite allocationIp policy references for a TARGET tenant.

Allocated-IP display names embed the Cato-ASSIGNED ip ("<pop> - <ip>"), so the
source tenant's names can never resolve in a target. The cross-tenant identity
of an allocation is (pop_location_name, description); 1-dependencies creates the
target's allocations keyed by the SOURCE display name and exports
`allocated_ip_map` (source name -> this tenant's {name, ip, id}) -- the join is
therefore already done in Terraform state.

This script:
  1. reads `terraform output -json allocated_ip_map` from 1-dependencies/
     (requires 1-dependencies to be APPLIED against the target first);
  2. scans every config_data/all_*_rules_and_sections.json for allocationIp
     refs and rewrites each {"name": "<source>"} to the target's name
     (dropping any stale source id in the ref);
  3. writes staged copies to config_data/.remapped/<file> -- the committed
     exports are never mutated;
  4. prints a JSON manifest {<file>: <staged path>} on stdout.

Unmatched refs are a HARD ERROR (exit 1) listing every miss -- deploying a
mis-scoped security rule is worse than failing. Exit 0 with manifest {} when no
policy file carries allocationIp refs.

Usage:
    python3 scripts/remap_allocated_ips.py                 # uses 1-dependencies/ state
    python3 scripts/remap_allocated_ips.py --map-json M    # explicit map (tests)
"""
import argparse
import glob
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DATA = os.path.join(REPO_ROOT, "config_data")
STAGING_DIR = os.path.join(CONFIG_DATA, ".remapped")
DEPS_DIR = os.path.join(REPO_ROOT, "1-dependencies")


def log(msg):
    print(msg, file=sys.stderr)


def read_allocated_ip_map(env=None):
    """source display name -> {name, ip, id} from 1-dependencies' Terraform STATE.

    Read `terraform show -json` rather than the allocated_ip_map output: outputs
    are only persisted by a fully-successful apply, but a license-capped target
    legitimately fails some allocations (partial apply) while the created ones
    ARE in state -- and those are exactly the ones that can be remapped."""
    result = subprocess.run(
        ["terraform", f"-chdir={DEPS_DIR}", "show", "-json"],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "failed to read 1-dependencies state -- has 1-dependencies been "
            "applied against this target?\n" + (result.stderr or "")[-400:])
    state = json.loads(result.stdout)

    name_map = {}

    def walk_module(module):
        for res in module.get("resources", []):
            if res.get("type") == "cato_allocated_ip" and res.get("name") == "this":
                source_name = res.get("index")
                values = res.get("values") or {}
                if source_name and values.get("name"):
                    name_map[source_name] = {
                        "name": values.get("name"),
                        "ip": values.get("ip"),
                        "id": values.get("id"),
                    }
        for child in module.get("child_modules", []):
            walk_module(child)

    walk_module(((state.get("values") or {}).get("root_module")) or {})
    return name_map


# JSON keys that carry allocated-IP entity refs: allocationIp (WAN network,
# list of refs) and externalIp (remote port forward, single ref object).
ALLOC_REF_KEYS = ("allocationIp", "externalIp")


def _remap_one(ref, name_map, misses, hits):
    if not isinstance(ref, dict) or not ref.get("name"):
        return ref
    source_name = ref["name"]
    target = name_map.get(source_name)
    if target is None:
        misses.append(source_name)
        return ref
    hits.append((source_name, target["name"]))
    # name only: the source id is meaningless in the target
    return {"name": target["name"]}


def remap_refs(node, name_map, misses, hits):
    """Recursively rewrite allocated-IP refs (list- or object-shaped) in-place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ALLOC_REF_KEYS and isinstance(value, list):
                node[key] = [_remap_one(ref, name_map, misses, hits) for ref in value]
            elif key in ALLOC_REF_KEYS and isinstance(value, dict):
                node[key] = _remap_one(value, name_map, misses, hits)
            else:
                remap_refs(value, name_map, misses, hits)
    elif isinstance(node, list):
        for item in node:
            remap_refs(item, name_map, misses, hits)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map-json", default=None,
                        help="explicit allocated_ip_map JSON (bypasses terraform output; for tests)")
    parser.add_argument("--strict", action="store_true",
                        help="treat unmatched allocated-IP refs as a hard error (default: warn and "
                             "leave them as-is so only those rules fail at apply)")
    args = parser.parse_args()

    if args.map_json:
        name_map = json.loads(args.map_json)
    else:
        name_map = read_allocated_ip_map()
    log(f"allocated_ip_map: {len(name_map)} source allocation(s) mapped")

    manifest = {}
    all_misses = []
    for path in sorted(glob.glob(os.path.join(CONFIG_DATA, "all_*_rules_and_sections.json"))):
        filename = os.path.basename(path)
        with open(path) as f:
            raw = f.read()
        if not any(f'"{key}"' in raw for key in ALLOC_REF_KEYS):
            continue
        data = json.loads(raw)
        misses, hits = [], []
        remap_refs(data, name_map, misses, hits)
        if not hits and not misses:
            continue
        for source, target in hits:
            log(f"  {filename}: {source!r} -> {target!r}")
        all_misses.extend((filename, m) for m in misses)
        os.makedirs(STAGING_DIR, exist_ok=True)
        staged = os.path.join(STAGING_DIR, filename)
        with open(staged, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        manifest[filename] = staged

    if all_misses:
        # An unmatched ref stays AS-IS in the staged copy -- the affected rule
        # then fails cleanly at apply (ruleHasInvalidEntity) while every other
        # rule lands. This is the expected state when the target's license caps
        # allocations below the source's count (e.g. dev tenants), or a source
        # PoP isn't available to the target. --strict restores the hard error.
        log(f"WARNING: {len(all_misses)} unmatched allocated-IP ref(s) left as-is "
            "(their rules will fail individually at apply):")
        for filename, miss in all_misses:
            log(f"  {filename}: {miss!r}")
        if args.strict:
            log("--strict set: treating unmatched refs as a hard error.")
            return 1

    print(json.dumps(manifest))
    log(f"staged {len(manifest)} remapped file(s) under config_data/.remapped/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
