output "deployment_summary" {
  description = "Counts of managed Client Connectivity objects."
  value       = module.cc_rules.deployment_summary
}

output "section_ids" {
  description = "Map of section names to their IDs."
  value       = module.cc_rules.section_ids
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.cc_rules.rule_ids
}

output "unordered_rules" {
  description = "rules_in_sections rows excluded from cato_bulk_cc_move_rule (no managed section to order within)."
  value       = module.cc_rules.unordered_rules
}
