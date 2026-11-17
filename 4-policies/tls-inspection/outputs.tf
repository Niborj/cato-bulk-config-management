output "deployment_summary" {
  description = "Summary statistics of the TLS Inspection deployment."
  value       = module.tls_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.tls_rules.rule_ids
}

output "section_ids" {
  description = "Map of section names to their IDs."
  value       = module.tls_rules.section_ids
}
