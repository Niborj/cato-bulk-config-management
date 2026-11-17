# Physical socket sites -- the full bulk module (sites + WAN/LAN interfaces +
# LAG members + network ranges + Wi-Fi SSIDs), fed from
# config_data/socket_sites.json. Moved verbatim from main.tf (state address
# module.sites unchanged).

module "sites" {
  # Local clone while wifi_ssid/static-host support is pre-release; restore the
  # registry pin below once the module is published with these features.
  source  = "catonetworks/socket-bulk-sites/cato"
  version = "0.0.14"
  # Local dev override (uncomment to use a local module clone):
  # source = "../../../catonetworks/terraform-cato-socket-bulk-sites"
  # version = "0.0.14"

  sites_json_file_path = "${var.config_data_dir}/socket_sites.json"
}
