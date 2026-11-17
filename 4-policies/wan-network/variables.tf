variable "cato_token" {
  description = "Cato API token with Edit permissions. Optional when cato_profile / CATO_PROFILE is used."
  type        = string
  sensitive   = true
  default     = null
}

variable "account_id" {
  description = "Cato account ID. Optional when cato_profile / CATO_PROFILE is used."
  type        = string
  default     = null
}

variable "cato_profile" {
  description = "Optional catocli profile name (from ~/.cato/credentials) to source token, account_id, and endpoint from. Explicit token/account_id values always win over profile-sourced values. May also be supplied via the CATO_PROFILE environment variable instead."
  type        = string
  default     = null
}

variable "wnw_rules_json_file_path" {
  description = "Path to the WAN Network rules/sections JSON. Overridden by scripts/bulk_apply.py with the allocated-IP-remapped staging copy (config_data/.remapped/) when deploying to a target tenant -- allocationIp display names embed the tenant-specific IP, so source names never resolve in a target. See scripts/remap_allocated_ips.py."
  type        = string
  default     = "../../config_data/all_wnw_rules_and_sections.json"
}

variable "allocated_ips_json_file_path" {
  description = "Path to the allocated-IPs export (catocli export allocated_ips) carrying each allocation's stable (description, popLocation) identity -- used to resolve NAT allocationIp refs against THIS tenant via the cato_allocatedIp data source."
  type        = string
  default     = "../../config_data/allocated_ips.json"
}
