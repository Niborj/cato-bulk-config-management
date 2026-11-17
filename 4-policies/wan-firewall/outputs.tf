output "deployment_summary" {
  description = "Summary statistics of the WAN Firewall deployment."
  value       = module.wf_rules.deployment_summary
}

output "rule_ids" {
  description = "Map of rule names to their IDs."
  value       = module.wf_rules.rule_ids
}

output "section_ids" {
  description = "Map of section names to their IDs."
  value       = module.wf_rules.section_ids
}
