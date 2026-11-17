provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}

module "if_rules" {
  source  = "catonetworks/bulk-if-rules/cato"
  version = "0.0.9"
  # Local dev override (uncomment to use a local module clone):
  # source = "../../../../catonetworks/terraform-cato-bulk-if-rules"
  ifw_rules_json_file_path = "${var.config_data_dir}/all_ifw_rules_and_sections.json"
  # NOTE: the if-rules module has NO allow_empty_policy flag -- it self-guards
  # dynamically (fails only if an empty JSON would wipe a non-empty LIVE policy;
  # greenfield-empty passes automatically), so no flag is needed or accepted.
}
