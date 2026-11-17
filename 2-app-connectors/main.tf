# 2-app-connectors -- ZTNA app connectors + private applications.
#
# Split out of the dependencies tier (2026-11-13) so they deploy AFTER sites
# (1-sites) but before groups/policies: connectors register against the live
# account and private apps publish through the connector GROUPS that
# cato_app_connector creates implicitly (group_name), and both have been the
# flakiest, most account-specific dependency types -- isolating them keeps a
# failed connector/private-app apply from sitting between the foundational
# dependency tier and sites.
#
# Native provider resources inline (no wrapper module): bulk_app_connectors.tf
# (cato_app_connector) and bulk_private_apps.tf (cato_private_app, which
# depends_on cato_app_connector.this). This file carries only the provider.

provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}
