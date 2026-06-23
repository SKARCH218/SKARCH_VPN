#!/bin/bash
###############################################################################
# OpenVPN server installer for this Raspberry Pi (Debian 13, Pi 5).
#
#   - 활성 인스턴스: UDP 1703, TCP 443
#   - TCP 1703 은 비활성 (stop + disable)
#   - Password auth (username/password), no client certificate
#   - NAT: 10.8.1.0/24 (udp-1703), 10.8.2.0/24 (tcp443)
#   - SD-card friendly: logs in RAM (journald volatile), status files on tmpfs
#   - Daily reboot at 06:00 KST
#
# Run with:   sudo bash install.sh
# Re-running is safe (idempotent).
###############################################################################
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "root 권한 필요:  sudo bash $0" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
WAN_IF="eth0"
DET_IF="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' || true)"
[ -n "$DET_IF" ] && WAN_IF="$DET_IF"
echo "==> WAN/NAT interface: $WAN_IF"

echo "==> Installing packages (openvpn, nftables)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq openvpn nftables

echo "==> Deploying server PKI + config to /etc/openvpn/server ..."
install -d -m 0750 /etc/openvpn/server
install -m 0644 "$SRC/pki/ca.crt"            /etc/openvpn/server/ca.crt
install -m 0644 "$SRC/pki/server.crt"        /etc/openvpn/server/server.crt
install -m 0600 "$SRC/pki/server.key"        /etc/openvpn/server/server.key
install -m 0600 "$SRC/server/checkpass.sh"   /etc/openvpn/server/checkpass.sh && chmod 0700 /etc/openvpn/server/checkpass.sh
install -m 0644 "$SRC/server/tcp443.conf"    /etc/openvpn/server/tcp443.conf
install -m 0644 "$SRC/server/udp-1703.conf"  /etc/openvpn/server/udp-1703.conf

# users 파일: 이미 /etc/openvpn/server/users 가 있으면 덮어쓰지 않음 (운영 중 추가된 유저 보호)
if [ ! -f /etc/openvpn/server/users ]; then
  install -m 0600 "$SRC/server/users" /etc/openvpn/server/users
  echo "  users 파일 복사 완료 (기존 없음)"
else
  echo "  users 파일 유지 (기존 파일 보호)"
fi

# NAT rules: fix the interface name, then deploy.
sed "s/\"eth0\"/\"$WAN_IF\"/g" "$SRC/system/openvpn-nat.nft" > /etc/openvpn/server/openvpn-nat.nft
chmod 0644 /etc/openvpn/server/openvpn-nat.nft

echo "==> Installing systemd / sysctl / tmpfiles / journald units ..."
install -m 0644 "$SRC/system/openvpn-nat.service"     /etc/systemd/system/openvpn-nat.service
install -m 0644 "$SRC/system/daily-reboot.service"    /etc/systemd/system/daily-reboot.service
install -m 0644 "$SRC/system/daily-reboot.timer"      /etc/systemd/system/daily-reboot.timer
install -m 0644 "$SRC/system/99-openvpn.conf"         /etc/sysctl.d/99-openvpn.conf
install -m 0644 "$SRC/system/openvpn-tmpfiles.conf"   /etc/tmpfiles.d/openvpn-server.conf
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 "$SRC/system/journald-volatile.conf"  /etc/systemd/journald.conf.d/volatile.conf

echo "==> Applying runtime settings ..."
sysctl -q --system
systemd-tmpfiles --create /etc/tmpfiles.d/openvpn-server.conf
systemctl restart systemd-journald
systemctl daemon-reload

echo "==> Disabling tcp-1703 (stop + disable, won't start on boot) ..."
systemctl disable --now openvpn-server@tcp-1703.service 2>/dev/null || true

echo "==> Enabling + starting active services ..."
systemctl enable --now openvpn-nat.service
systemctl enable --now openvpn-server@tcp443.service
systemctl enable --now openvpn-server@udp-1703.service
systemctl enable --now daily-reboot.timer

# NAT 재적용 (서브넷이 바뀌었을 수 있으므로)
systemctl restart openvpn-nat.service

echo
echo "=========================== STATUS ==========================="
for s in openvpn-nat "openvpn-server@tcp443" "openvpn-server@udp-1703" "openvpn-server@tcp-1703" daily-reboot.timer; do
  printf '  %-36s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null)"
done
echo "  ip_forward  $(cat /proc/sys/net/ipv4/ip_forward)"
echo "--- 리스닝 포트 ---"
ss -tulnp 2>/dev/null | grep -E ':(443|1703)' || echo "  (없음)"
echo "--- next scheduled reboot ---"
systemctl list-timers daily-reboot.timer --no-pager 2>/dev/null | sed -n '1,2p'
echo "=============================================================="
echo ""
echo "클라이언트 파일:"
echo "  TCP 443  → $SRC/client/client-tcp443.ovpn"
echo "  UDP 1703 → $SRC/client/client-udp1703.ovpn"
echo ""
echo "유저 관리:"
echo "  sudo bash $SRC/scripts/manage-users.sh list"
echo "  sudo bash $SRC/scripts/manage-users.sh add <유저명> <비밀번호>"
echo "  sudo bash $SRC/scripts/manage-users.sh del <유저명>"
echo "  sudo bash $SRC/scripts/manage-users.sh passwd <유저명> <새비밀번호>"
