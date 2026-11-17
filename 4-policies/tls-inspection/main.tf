provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}

module "tls_rules" {
  source  = "catonetworks/bulk-tls-rules/cato"
  version = "0.0.2"
  # Local dev override (uncomment to use a local module clone):
  # source = "../../../../catonetworks/terraform-cato-bulk-tls-rules"
  tls_rules_json_file_path = "${var.config_data_dir}/all_tls_rules_and_sections.json"
  allow_empty_policy       = true
}
