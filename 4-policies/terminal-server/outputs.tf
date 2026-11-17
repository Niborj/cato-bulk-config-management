output "deployment_summary" {
  description = "Counts of managed Terminal Server objects."
  value       = module.ts_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule name to rule id."
  value       = module.ts_rules.rule_ids
}

output "section_ids" {
  description = "Map of section name to section id."
  value       = module.ts_rules.section_ids
}

output "unordered_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_ts_move_rule (no managed section to order within)."
  value       = module.ts_rules.unordered_rules
}
