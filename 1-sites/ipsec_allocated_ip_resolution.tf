# Dynamic allocated-IP resolution for IPSec site publicCatoIp.
#
# An IPSec site with an IPv4 destination terminates on a Cato-side public IP
# (publicCatoIp), which is a Cato *allocated IP*. The allocation id is
# Cato-ASSIGNED per tenant, so ipsec_sites.json references it by its display
# NAME ("<pop> - <ip>"); the stable cross-tenant identity is
# (description, popLocation), carried in config_data/allocated_ips.json (same
# convention as 3-policies/wan-network/allocated_ip_resolution.tf).
#
# Each distinct name referenced by an IPSec endpoint (primary/secondary) is
# resolved against THIS tenant via the cato_allocatedIp data source ([0] first
# record) and fed into cato_ipsec_site.ipsec.{primary,secondary}.public_cato_ip_id.
# Error semantics:
#   - a referenced allocation that does NOT resolve on this tenant fails the
#     plan with a clear message (postcondition below) -- create/adopt the
#     allocation in 1-dependencies (cato_allocated_ip) before applying 0-sites;
#   - FQDN endpoints carry no public_cato_ip_name and are skipped (no lookup).

locals {
  ipsec_allocated_ips_source = try(jsondecode(file("${var.config_data_dir}/allocated_ips.json")), [])

  ipsec_alloc_identity_by_name = {
    for a in local.ipsec_allocated_ips_source :
    a.name => {
      description = try(a.description, "")
      pop         = a.popLocation.name
    }
  }

  # Distinct allocation display names referenced by IPSec site endpoints
  # (primary + secondary across all sites in ipsec_sites.json).
  ipsec_referenced_alloc_names = toset(compact(flatten([
    for s in local.ipsec_sites_data : [
      try(s.ipsec.primary.public_cato_ip_name, ""),
      try(s.ipsec.secondary.public_cato_ip_name, ""),
    ]
  ])))

  ipsec_alloc_lookups = {
    for name in local.ipsec_referenced_alloc_names :
    name => local.ipsec_alloc_identity_by_name[name]
    if name != "" && contains(keys(local.ipsec_alloc_identity_by_name), name)
  }
}

data "cato_allocatedIp" "ipsec" {
  for_each = local.ipsec_alloc_lookups

  description_filter  = [each.value.description]
  pop_location_filter = [each.value.pop]

  lifecycle {
    postcondition {
      condition = length(self.items) > 0
      error_message = format(
        "IPSec site references allocated IP %q (description %q, PoP %q), but no such allocation exists on this tenant. Create/adopt the allocation (1-dependencies cato_allocated_ip) before applying 0-sites.",
        each.key, each.value.description, each.value.pop,
      )
    }
  }
}

locals {
  # IDs (not names): resolve each referenced allocation name to its target-tenant
  # allocated-IP id for public_cato_ip_id.
  ipsec_public_cato_ip_id_by_name = {
    for name, d in data.cato_allocatedIp.ipsec :
    name => d.items[0].id
  }
}
