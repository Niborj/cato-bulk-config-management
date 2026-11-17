output "deployment_summary" {
  description = "Counts of managed App Tenant Restriction objects."
  value       = module.atr_rules.deployment_summary
}

output "section_ids" {
  description = "Map of section names to their IDs."
  value       = module.atr_rules.section_ids
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.atr_rules.rule_ids
}

output "unordered_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_atr_move_rule (no managed section to order within)."
  value       = module.atr_rules.unordered_rules
}
