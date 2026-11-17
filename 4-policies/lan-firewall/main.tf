provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}

module "lan_fw_rules" {
  # Local clone while the module is pre-release; restore the registry pin
  # below once it's published.
  source  = "catonetworks/bulk-lan-fw-rules/cato"
  version = "0.0.1"
  # Local dev override (uncomment to use a local module clone):
  # source = "../../../../catonetworks/terraform-cato-bulk-lan-fw-rules"
  # version = "0.0.1"

  lan_fw_rules_json_file_path = "${var.config_data_dir}/all_lan_fw_rules_and_sections.json"
  allow_empty_policy          = true
}
