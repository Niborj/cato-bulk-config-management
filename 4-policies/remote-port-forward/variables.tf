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

variable "rpf_rules_json_file_path" {
  description = "Path to the Remote Port Forward rules/sections JSON. Overridden by scripts/bulk_apply.py with the allocated-IP-remapped staging copy (config_data/.remapped/) when deploying to a target tenant -- RPF rules reference allocated IPs (externalIp) whose display names embed the tenant-specific IP. See scripts/remap_allocated_ips.py."
  type        = string
  default     = "../../config_data/all_rpf_rules_and_sections.json"
}

variable "allocated_ips_json_file_path" {
  description = "Path to the allocated-IPs export (catocli export allocated_ips) carrying each allocation's stable (description, popLocation) identity -- used to resolve rule externalIp refs against THIS tenant via the cato_allocatedIp data source."
  type        = string
  default     = "../../config_data/allocated_ips.json"
}
