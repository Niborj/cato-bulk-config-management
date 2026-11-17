output "app_connector_ids" {
  description = "Map of app connector name => Cato app connector ID."
  value       = { for name, c in cato_app_connector.this : name => c.id }
}

output "private_app_ids" {
  description = "Map of private application name => Cato private application ID."
  value       = { for name, p in cato_private_app.this : name => p.id }
}

output "deployment_summary" {
  description = "Counts of managed app connectors and private applications."
  value = {
    total_app_connectors = length(cato_app_connector.this)
    total_private_apps   = length(cato_private_app.this)
  }
}
