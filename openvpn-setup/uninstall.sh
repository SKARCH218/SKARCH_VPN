#!/bin/bash
# Reverses install.sh. Run with: sudo bash uninstall.sh
# (Leaves the openvpn/nftables packages installed; pass --purge to remove them too.)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo bash $0" >&2; exit 1; }

systemctl disable --now openvpn-server@tcp-1703.service 2>/dev/null || true
systemctl disable --now openvpn-server@udp-1703.service 2>/dev/null || true
systemctl disable --now openvpn-nat.service             2>/dev/null || true
systemctl disable --now daily-reboot.timer              2>/dev/null || true
nft delete table inet openvpn_nat 2>/dev/null || true

rm -f /etc/systemd/system/openvpn-nat.service \
      /etc/systemd/system/daily-reboot.service \
      /etc/systemd/system/daily-reboot.timer \
      /etc/sysctl.d/99-openvpn.conf \
      /etc/tmpfiles.d/openvpn-server.conf \
      /etc/systemd/journald.conf.d/volatile.conf \
      /etc/openvpn/server/{ca.crt,server.crt,server.key,users,checkpass.sh,tcp-1703.conf,udp-1703.conf,openvpn-nat.nft}
systemctl daemon-reload
systemctl restart systemd-journald 2>/dev/null || true
echo "Removed OpenVPN server config + units. (ip_forward stays until reboot.)"

if [ "${1:-}" = "--purge" ]; then
  DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq openvpn || true
  echo "openvpn package removed."
fi
