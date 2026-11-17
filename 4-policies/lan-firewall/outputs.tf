output "deployment_summary" {
  description = "Counts of managed LAN firewall objects."
  value       = module.lan_fw_rules.deployment_summary
}

output "section_ids" {
  description = "Map of main section name to section id."
  value       = module.lan_fw_rules.section_ids
}

output "sub_section_ids" {
  description = "Map of '<sub_policy>::<section>' to section id."
  value       = module.lan_fw_rules.sub_section_ids
}

output "sub_policy_ids" {
  description = "Map of sub-policy name to sub-policy id."
  value       = module.lan_fw_rules.sub_policy_ids
}

output "scope_rule_ids" {
  description = "Map of sub-policy name to its SUB_POLICY_SCOPE rule id."
  value       = module.lan_fw_rules.scope_rule_ids
}

output "network_rule_ids" {
  description = "Map of network rule name to rule id."
  value       = module.lan_fw_rules.network_rule_ids
}

output "firewall_rule_ids" {
  description = "Map of '<network_rule>::<firewall_rule>' to rule id."
  value       = module.lan_fw_rules.firewall_rule_ids
}

output "unordered_network_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_lf_move_rule (no managed section to order within)."
  value       = module.lan_fw_rules.unordered_network_rules
}
