output "deployment_summary" {
  description = "Counts of managed Private Access objects."
  value       = module.pa_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.pa_rules.rule_ids
}
