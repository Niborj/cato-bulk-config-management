# Physical socket sites -- the full bulk module (sites + WAN/LAN interfaces +
# LAG members + network ranges + Wi-Fi SSIDs), fed from
# config_data/socket_sites.json. Moved verbatim from main.tf (state address
# module.sites unchanged).

module "sites" {
  # Local clone while wifi_ssid/static-host support is pre-release; restore the
  # registry pin below once the module is published with these features.
  # GREENFIELD TEST (2026-11-24): registry 0.0.14 reads lan.id / nr.id
  # unguarded, so a `catocli export all --strip-ids` capture fails to plan
  # ("This object does not have an attribute named id"). Using a local clone of
  # v0.0.14 + a two-line try() fix until a patched release is published.
  # source  = "catonetworks/socket-bulk-sites/cato"
  # version = "0.0.14"
  source = "../../modules/terraform-cato-socket-bulk-sites"

  sites_json_file_path = "${var.config_data_dir}/socket_sites.json"
}
