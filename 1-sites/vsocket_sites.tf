# vSocket (cloud) PLACEHOLDER sites, fed from config_data/vsocket_sites.json
# (catocli export vsocket_sites). Creates the site shell only -- vSocket
# deployment/registration, HA secondaries (cato_secondary_*_vsocket), and BW
# licenses remain manual/follow-up steps. Missing JSON file => empty for_each
# (clean plan on accounts without cloud sites).
#
# The export carries the snapshot connType (legacy VSOCKET_VGX_* values for
# deployed vSockets), but cato_socket_site's connection_type accepts only the
# SiteConnectionTypeEnum SOCKET_* values -- map accordingly.

locals {
  vsocket_sites_data = try(jsondecode(file("${var.config_data_dir}/vsocket_sites.json")).sites, [])

  # snapshot ProtoType -> creatable SiteConnectionTypeEnum
  cloud_connection_type_map = {
    "VSOCKET_VGX_AWS"   = "SOCKET_AWS1500"
    "VSOCKET_VGX_AZURE" = "SOCKET_AZ1500"
    "VSOCKET_VGX_ESX"   = "SOCKET_ESX1500"
    "VSOCKET_VGX"       = "SOCKET_ESX1500"
    "SOCKET_AWS1500"    = "SOCKET_AWS1500"
    "SOCKET_AZ1500"     = "SOCKET_AZ1500"
    "SOCKET_GCP1500"    = "SOCKET_GCP1500"
    "SOCKET_ESX1500"    = "SOCKET_ESX1500"
  }
}

resource "cato_socket_site" "vsocket" {
  for_each = { for s in local.vsocket_sites_data : s.name => s }

  name            = each.value.name
  description     = try(each.value.description, "")
  connection_type = local.cloud_connection_type_map[each.value.connection_type]
  site_type       = each.value.type

  native_range = {
    native_network_range = each.value.native_range.subnet
    local_ip             = try(each.value.native_range.local_ip, cidrhost(each.value.native_range.subnet, 1))
  }

  site_location = {
    country_code = each.value.site_location.countryCode
    timezone     = each.value.site_location.timezone
    state_code   = try(each.value.site_location.stateCode, "") != "" ? each.value.site_location.stateCode : null
    city         = try(each.value.site_location.city, "") != "" ? each.value.site_location.city : null
  }

  lifecycle {
    # Placeholders stand in for externally-managed vSocket deployments --
    # never destroy a live cloud site because the export shrank.
    prevent_destroy = false
  }
}

output "vsocket_sites" {
  description = "Map of cloud/vSocket placeholder sites by name -> site id."
  value       = { for name, s in cato_socket_site.vsocket : name => s.id }
}
