# App Connectors -- ZTNA / private access infrastructure (added 2026-11-10),
# fed from config_data/app_connectors.json. Their group_name implicitly creates
# the connector groups that private apps' published_app_domain references.
# Policies reference the connector groups (created implicitly by
# cato_app_connector's group_name) by name.

locals {
  app_connectors_data = jsondecode(file("${var.config_data_dir}/app_connectors.json"))
  app_connectors      = try(local.app_connectors_data.connectors, [])
}

resource "cato_app_connector" "this" {
  for_each = { for c in local.app_connectors : c.name => c }

  name        = each.value.name
  description = try(each.value.description, null)
  group_name  = each.value.groupName
  type        = each.value.type

  location = {
    address      = try(each.value.location.address, null)
    city_name    = each.value.location.cityName
    country_code = each.value.location.countryCode
    state_code   = try(each.value.location.stateCode, null)
    timezone     = each.value.location.timezone
  }

  # idName refs accept exactly ONE of name/id -- reference by NAME per the repo
  # convention (PoP location names are global Cato constants, so names are
  # portable across tenants).
  preferred_pop_location = try(each.value.preferredPopLocation, null) == null ? null : {
    automatic      = try(each.value.preferredPopLocation.automatic, null)
    preferred_only = try(each.value.preferredPopLocation.preferredOnly, null)
    primary = try(each.value.preferredPopLocation.primary, null) == null ? null : {
      name = each.value.preferredPopLocation.primary.name
    }
    secondary = try(each.value.preferredPopLocation.secondary, null) == null ? null : {
      name = each.value.preferredPopLocation.secondary.name
    }
  }
}
