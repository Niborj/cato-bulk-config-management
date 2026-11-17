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

variable "config_data_dir" {
  description = "Directory holding the exported config_data/*.json files this folder reads. Defaults to the repo-relative path; override (or set TF_VAR_config_data_dir) to point at an external data set."
  type        = string
  default     = "../../config_data"
}
