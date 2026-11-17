output "deployment_summary" {
  description = "Summary statistics of the WAN Network deployment."
  value       = module.wnw_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.wnw_rules.rule_ids
}

output "section_ids" {
  description = "Map of section names to their IDs."
  value       = module.wnw_rules.section_ids
}
