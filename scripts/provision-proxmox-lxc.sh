#!/usr/bin/env bash
# Generic Ubuntu LXC provisioner for homelab-control services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lxc.env"

if ! pvesm path "${UBUNTU_TEMPLATE}" >/dev/null 2>&1; then
  echo "Template not found: ${UBUNTU_TEMPLATE}" >&2
  exit 1
fi

pct create "${CT_ID}" "${UBUNTU_TEMPLATE}" \
  --hostname "${CT_HOSTNAME}" \
  --cores "${CT_CORES}" \
  --memory "${CT_MEMORY_MB}" \
  --swap "${CT_SWAP_MB}" \
  --rootfs "${CT_STORAGE}:${CT_DISK_GB}" \
  --net0 "name=eth0,bridge=${CT_BRIDGE},ip=dhcp" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1 \
  --start 1

pct exec "${CT_ID}" -- bash -s <<EOF
set -e
cat > /etc/network/interfaces <<'INET'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet static
  address ${LXC_IP}
  netmask ${LXC_NETMASK}
  gateway ${LXC_GATEWAY}
  dns-nameservers ${PIHOLE_DNS_IP}
INET
systemctl restart networking || true
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl git jq ca-certificates rsync
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
systemctl enable docker
systemctl start docker
EOF

echo "Provisioned ${CT_HOSTNAME} (${CT_ID}) at ${LXC_IP}"
