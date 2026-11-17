output "deployment_summary" {
  description = "Counts of managed SDP Static IP Allocation objects."
  value       = module.ssia_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule name to rule id."
  value       = module.ssia_rules.rule_ids
}

output "section_ids" {
  description = "Map of section name to section id."
  value       = module.ssia_rules.section_ids
}

output "unordered_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_ssia_move_rule (no managed section to order within)."
  value       = module.ssia_rules.unordered_rules
}
