provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}

module "swp_rules" {
  # Local clone while the module is pre-release; restore the registry pin
  # below once it's published.
  source  = "catonetworks/bulk-swp-rules/cato"
  version = "0.0.1"
  # Local dev override (uncomment to use a local module clone):
  # source = "../../../../catonetworks/terraform-cato-bulk-swp-rules"
  # version = "0.0.1"

  swp_rules_json_file_path = "${var.config_data_dir}/all_swp_rules_and_sections.json"
  # Site Web Proxy policy is empty in this account (0 rules); allow the empty
  # policy so the guard doesn't block plan/apply. No cato_swpRulesIndex data
  # source exists, so the dynamic (no-flag) guard used for IFW isn't available here.
  allow_empty_policy = true
}
