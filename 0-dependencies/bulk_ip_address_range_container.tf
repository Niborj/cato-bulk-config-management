# IP Address Range Containers -- fed from config_data/ip_range_containers.json.
# Name and mode are replace-on-change; description is a required attribute
# (exported as "" when unset). INLINE containers own their full member set via
# `values`; URL containers carry sync settings instead.

locals {
  ip_range_containers = jsondecode(file("${var.config_data_dir}/ip_range_containers.json"))
}

resource "cato_ip_address_range_container" "this" {
  for_each = { for c in local.ip_range_containers : c.name => c }

  name        = each.value.name
  mode        = each.value.mode
  description = try(each.value.description, "")

  values = each.value.mode == "INLINE" ? [
    for v in try(each.value.values, []) : { from = v.from, to = v.to }
  ] : null
  file_type = try(each.value.file_type, null)
  sync_data = try(each.value.sync_data, null)
}
