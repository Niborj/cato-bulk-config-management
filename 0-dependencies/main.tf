# 1-dependencies -- account-level objects the policy folders reference by name.
# Each dependency type lives in its own bulk_<type>.tf file (one native provider
# resource + its config_data JSON local + a brief header comment). This file
# carries only the provider configuration.

provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}
