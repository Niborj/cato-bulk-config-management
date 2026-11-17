output "deployment_summary" {
  description = "Counts of managed Application Control objects."
  value       = module.apc_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule name to rule id."
  value       = module.apc_rules.rule_ids
}

output "section_ids" {
  description = "Map of section name to section id."
  value       = module.apc_rules.section_ids
}

output "unordered_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_apc_move_rule (no managed section to order within)."
  value       = module.apc_rules.unordered_rules
}
