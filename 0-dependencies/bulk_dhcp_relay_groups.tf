# DHCP Relay Groups -- fed from config_data/dhcp_relay_groups.json (optional
# until the export exists -- see scripts/validate_config_data.py).
# DHCP relay group ids are opaque (not legacy numeric entity ids); the provider
# resolves/recreates by name, so there's no name-vs-id resolution concern here.

locals {
  dhcp_relay_groups = jsondecode(file("${var.config_data_dir}/dhcp_relay_groups.json"))
}

resource "cato_dhcp_relay_group" "this" {
  for_each = { for d in local.dhcp_relay_groups : d.name => d }

  name   = each.value.name
  server = each.value.server
}
