# Private Applications -- ZTNA / private access infrastructure (added
# 2026-11-10), fed from config_data/private_applications.json. Reference
# connector groups by name, so the connectors (which create the groups) must
# exist first.

locals {
  private_applications = jsondecode(file("${var.config_data_dir}/private_applications.json"))
}

resource "cato_private_app" "this" {
  depends_on = [cato_app_connector.this]
  for_each   = { for p in local.private_applications : p.name => p }

  name                 = each.value.name
  description          = try(each.value.description, null)
  internal_app_address = each.value.internalAppAddress
  allow_icmp_protocol  = try(each.value.allowIcmpProtocol, false)
  probing_enabled      = try(each.value.probingEnabled, false)
  published            = try(each.value.published, false)

  private_app_probing = try(each.value.privateAppProbing, null) == null ? null : {
    type                 = each.value.privateAppProbing.type
    interval             = each.value.privateAppProbing.interval
    fault_threshold_down = each.value.privateAppProbing.faultThresholdDown
  }

  protocol_ports = [
    for pp in try(each.value.protocolPorts, []) : {
      protocol = pp.protocol
      ports    = try(pp.port, null)
      port_range = try(pp.portRange, null) == null ? null : {
        from = pp.portRange.from
        to   = pp.portRange.to
      }
    }
  ]

  published_app_domain = try(each.value.publishedAppDomain, null) == null ? null : {
    published_app_domain = each.value.publishedAppDomain.publishedAppDomain
    connector_group_name = each.value.publishedAppDomain.connectorGroupName
  }
}
