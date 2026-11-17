output "deployment_summary" {
  description = "Counts of managed Always-On objects."
  value       = module.ao_rules.deployment_summary
}

output "section_ids" {
  description = "Map of section names to their IDs."
  value       = module.ao_rules.section_ids
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.ao_rules.rule_ids
}

output "unordered_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_ao_move_rule (no managed section to order within)."
  value       = module.ao_rules.unordered_rules
}
