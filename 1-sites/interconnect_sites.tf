# Cloud Interconnect PLACEHOLDER sites, fed from
# config_data/interconnect_sites.json (catocli export interconnect_sites).
# Creates the site metadata only -- physical connections
# (cato_cloud_interconnect_physical_connection) and BGP peers are separate,
# later steps. Missing/empty JSON => empty for_each.

locals {
  interconnect_sites_data = try(jsondecode(file("${var.config_data_dir}/interconnect_sites.json")).sites, [])
}

resource "cato_cloud_interconnect_site" "this" {
  for_each = { for s in local.interconnect_sites_data : s.name => s }

  name        = each.value.name
  description = try(each.value.description, null)
  site_type   = each.value.type

  site_location = {
    country_code = each.value.site_location.countryCode
    timezone     = each.value.site_location.timezone
    state_code   = try(each.value.site_location.stateCode, "") != "" ? each.value.site_location.stateCode : null
    city         = try(each.value.site_location.city, "") != "" ? each.value.site_location.city : null
  }
}

output "interconnect_sites" {
  description = "Map of Cloud Interconnect placeholder sites by name -> site id."
  value       = { for name, s in cato_cloud_interconnect_site.this : name => s.id }
}
