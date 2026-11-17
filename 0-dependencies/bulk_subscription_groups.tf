# Subscription Groups -- fed from config_data/subscription_groups.json.
# References mailing lists and integrations (e.g. webhooks) by name for
# portability; ids stay in the JSON for import only.

locals {
  subscription_groups = jsondecode(file("${var.config_data_dir}/subscription_groups.json"))
}

resource "cato_subscription_group" "this" {
  for_each = { for s in local.subscription_groups : s.name => s }

  # Notification chain ordering: subscription groups reference mailing lists
  # AND webhooks (integration[]) by name below, so both must exist first.
  depends_on = [cato_mailing_list.this, cato_webhook.this]

  # Referenced by name for portability; ids stay in the JSON for import only.
  name = each.value.name
  mailing_list = length(try(each.value.mailing_list, [])) > 0 ? [
    for m in each.value.mailing_list : { id = null, name = m.name }
  ] : null
  integration = length(try(each.value.integration, [])) > 0 ? [
    for i in each.value.integration : { id = null, name = i.name }
  ] : null
}
