provider "cato" {
  # baseurl    = "https://api.catonetworks.com/api/v1/graphql2"
  # token      = var.cato_token
  # account_id = var.account_id
  profile = var.cato_profile
}

# Site resources are split by site TYPE, one file each:
#   socket_sites.tf        physical socket sites (bulk module, socket_sites.json)
#   vsocket_sites.tf       vSocket/cloud placeholder sites (vsocket_sites.json)
#   ipsec_sites.tf         IPSec placeholder sites (ipsec_sites.json)
#   interconnect_sites.tf  Cloud Interconnect placeholder sites (interconnect_sites.json)
# static_hosts.tf carries the socket-site static hosts.
