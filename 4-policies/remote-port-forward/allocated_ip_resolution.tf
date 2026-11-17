# Dynamic allocated-IP resolution (description + popLocation).
#
# RPF rules reference allocated IPs by CMA display name ("<pop> - <ip>"), and
# the ip is Cato-ASSIGNED per tenant -- a source tenant's name never resolves
# in a target. The stable cross-tenant identity is (description, popLocation),
# carried for every allocation in config_data/allocated_ips.json.
#
# This file resolves each distinct (description, pop) pair referenced by the
# rules against THIS tenant via the cato_allocatedIp data source and feeds the
# module a source-name -> target-name map. Error semantics:
#   - a referenced allocation that does NOT resolve on this tenant fails the
#     plan with a clear message (precondition below) -- never a silent
#     pass-through of a stale name;
#   - rules with no external IP value are untouched (no lookup, no error).

locals {
  rpf_allocated_ips_source = jsondecode(file(var.allocated_ips_json_file_path))

  # Source display name -> stable identity. Names referenced by rules but
  # absent from allocated_ips.json pass through unmapped (lookup fallback).
  rpf_alloc_identity_by_name = {
    for a in local.rpf_allocated_ips_source :
    a.name => {
      description = try(a.description, "")
      pop         = a.popLocation.name
    }
  }

  # Distinct external-IP display names actually referenced by the RPF rules.
  rpf_rules_doc = jsondecode(file(var.rpf_rules_json_file_path))
  rpf_referenced_alloc_names = toset(compact([
    for rw in try(local.rpf_rules_doc.data.policy.remotePortFwd.policy.rules, []) :
    try(rw.rule.externalIp.name, "")
  ]))

  # Only referenced names that have a known identity get a data-source lookup.
  rpf_alloc_lookups = {
    for name in local.rpf_referenced_alloc_names :
    name => local.rpf_alloc_identity_by_name[name]
    if contains(keys(local.rpf_alloc_identity_by_name), name)
  }
}

data "cato_allocatedIp" "rpf" {
  for_each = local.rpf_alloc_lookups

  description_filter  = [each.value.description]
  pop_location_filter = [each.value.pop]

  lifecycle {
    postcondition {
      condition = length(self.items) > 0
      error_message = format(
        "RPF rule references allocated IP %q, but no allocation with description %q in PoP %q exists on this tenant. Create/adopt the allocation (1-dependencies) before applying this policy.",
        each.key, each.value.description, each.value.pop,
      )
    }
  }
}

locals {
  # Source display name -> THIS tenant's allocation ID, taking the first
  # record when the identity matches multiple allocations. IDs (not names):
  # the API's by-NAME resolver for AllocatedIp refs is unreliable -- by-ID
  # always resolves (CLI-verified on <test-profile>).
  rpf_allocated_ip_id_map = {
    for name, d in data.cato_allocatedIp.rpf :
    name => d.items[0].id
  }
}
