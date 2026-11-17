output "socket_sites" {
  description = "Map of all socket site resources by site name."
  value       = module.sites.socket_sites
}

output "wan_interfaces" {
  description = "Map of WAN interfaces by site name."
  value       = module.sites.wan_interfaces
}

output "network_ranges" {
  description = "Map of network ranges by site name."
  value       = module.sites.network_ranges
}
