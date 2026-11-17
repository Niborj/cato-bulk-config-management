# Mailing Lists -- fed from config_data/mailing_lists.json.
# Admin refs require ids (name rides along for readability); at least one
# address or admin must exist per list. System/default lists (e.g. "All Admins",
# id -100) always exist in the tenant, carry no address or admin, and the API
# rejects any attempt to create them as empty -- skip them here. The subscription
# groups still reference them by name, resolving against the live list.

locals {
  mailing_lists = jsondecode(file("${var.config_data_dir}/mailing_lists.json"))
}

resource "cato_mailing_list" "this" {
  for_each = {
    for m in local.mailing_lists : m.name => m
    if length(try(m.address, [])) > 0 || length(try(m.admin, [])) > 0
  }

  name    = each.value.name
  address = length(try(each.value.address, [])) > 0 ? each.value.address : null
  admin = length(try(each.value.admin, [])) > 0 ? [
    for a in each.value.admin : { id = a.id, name = try(a.name, null) }
  ] : null
}
