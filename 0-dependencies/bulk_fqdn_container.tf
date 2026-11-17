# FQDN Containers -- fed from config_data/fqdn_containers.json.
# Name and mode are replace-on-change; description is a required attribute
# (exported as "" when unset). INLINE containers own their full member set via
# `values`; URL containers carry sync settings instead.

locals {
  fqdn_containers = jsondecode(file("${var.config_data_dir}/fqdn_containers.json"))
}

resource "cato_fqdn_container" "this" {
  for_each = { for c in local.fqdn_containers : c.name => c }

  name        = each.value.name
  mode        = each.value.mode
  description = try(each.value.description, "")

  values    = each.value.mode == "INLINE" ? try(each.value.values, []) : null
  file_type = try(each.value.file_type, null)
  sync_data = try(each.value.sync_data, null)
}
