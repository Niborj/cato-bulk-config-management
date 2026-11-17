# Custom Applications -- fed from config_data/custom_applications.json.
# Native provider resource (as of provider 1.5.0-prerelease.1; previously a
# catocli/terraform_data wrapper -- removed).

locals {
  custom_applications = jsondecode(file("${var.config_data_dir}/custom_applications.json"))
}

resource "cato_custom_application" "this" {
  for_each = { for a in local.custom_applications : a.name => a }

  name        = each.value.name
  description = try(each.value.description, null)

  category = length(try(each.value.category, [])) > 0 ? [
    for c in each.value.category : {
      id   = c.id
      name = c.name
    }
  ] : null

  criteria = [
    for c in each.value.criteria : {
      protocol   = c.protocol
      port       = [for p in try(c.port, []) : tostring(p)]
      port_range = [for pr in try(c.portRange, []) : { from = tostring(pr.from), to = tostring(pr.to) }]
      destination = try(c.destination, null) == null ? null : {
        domain = try(c.destination.domain, [])
        fqdn   = try(c.destination.fqdn, [])
        destination_ip = try(c.destination.destinationIp, null) == null ? null : {
          ip       = try(c.destination.destinationIp.ip, [])
          subnet   = try(c.destination.destinationIp.subnet, [])
          ip_range = [for r in try(c.destination.destinationIp.ipRange, []) : { from = r.from, to = r.to }]
        }
      }
    }
  ]
}
