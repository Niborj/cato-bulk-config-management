output "group_ids" {
  description = "Map of group name => Cato group ID."
  value       = { for name, g in cato_group.this : name => g.id }
}

output "group_names" {
  description = "Names of all managed groups."
  value       = sort(keys(cato_group.this))
}

output "deployment_summary" {
  description = "Counts of managed group objects."
  value = {
    total_groups = length(cato_group.this)
  }
}

output "groups_skipped_unmanaged_site" {
  description = "Groups omitted because a site-scoped member references a site not in socket_sites.json (cloud/unmanaged site). Restored once those sites are replicated."
  value       = local.skipped_groups
}
