provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}

module "wf_rules" {
  source  = "catonetworks/bulk-wf-rules/cato"
  version = "0.0.10"
  # Local dev override (uncomment to use a local module clone):
  # source = "../../../../catonetworks/terraform-cato-bulk-wf-rules"
  wf_rules_json_file_path = "${var.config_data_dir}/all_wf_rules_and_sections.json"
  allow_empty_policy      = true
}
