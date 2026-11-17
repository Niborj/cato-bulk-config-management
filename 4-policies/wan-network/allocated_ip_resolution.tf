# Dynamic allocated-IP resolution (description + popLocation).
#
# WAN-network NAT rules reference allocated IPs by CMA display name
# ("<pop> - <ip>") in configuration.allocationIp[] -- a LIST per rule (multi
# routed IPs). The ip is Cato-ASSIGNED per tenant, so source names never
# resolve in a target; the stable cross-tenant identity is
# (description, popLocation), carried in config_data/allocated_ips.json.
#
# Each distinct (description, pop) pair referenced by the rules is resolved
# against THIS tenant via the cato_allocatedIp data source ([0] first record),
# and the module receives a source-name -> target-name map. Error semantics:
#   - a referenced allocation that does NOT resolve on this tenant fails the
#     plan with a clear message (postcondition below);
#   - rules with an empty allocationIp list are untouched (no lookup, no error).

locals {
  wnw_allocated_ips_source = jsondecode(file(var.allocated_ips_json_file_path))

  wnw_alloc_identity_by_name = {
    for a in local.wnw_allocated_ips_source :
    a.name => {
      description = try(a.description, "")
      pop         = a.popLocation.name
    }
  }

  # Distinct allocation display names referenced across all NAT rules.
  wnw_rules_doc = jsondecode(file(var.wnw_rules_json_file_path))
  wnw_referenced_alloc_names = toset(flatten([
    for rw in try(local.wnw_rules_doc.data.policy.wanNetwork.policy.rules, []) : [
      for ip in try(rw.rule.configuration.allocationIp, []) : try(ip.name, "")
    ]
  ]))

  wnw_alloc_lookups = {
    for name in local.wnw_referenced_alloc_names :
    name => local.wnw_alloc_identity_by_name[name]
    if name != "" && contains(keys(local.wnw_alloc_identity_by_name), name)
  }
}

data "cato_allocatedIp" "wnw" {
  for_each = local.wnw_alloc_lookups

  description_filter  = [each.value.description]
  pop_location_filter = [each.value.pop]

  lifecycle {
    postcondition {
      condition = length(self.items) > 0
      error_message = format(
        "WAN-network NAT rule references allocated IP %q, but no allocation with description %q in PoP %q exists on this tenant. Create/adopt the allocation (1-dependencies) before applying this policy.",
        each.key, each.value.description, each.value.pop,
      )
    }
  }
}

locals {
  # IDs (not names): the API's by-NAME resolver for AllocatedIp refs is
  # unreliable -- by-ID always resolves (CLI-verified on <test-profile>).
  wnw_allocated_ip_id_map = {
    for name, d in data.cato_allocatedIp.wnw :
    name => d.items[0].id
  }
}
