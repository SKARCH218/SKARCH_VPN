#!/bin/bash
# OpenVPN 관리자 패널 설치 스크립트
# 실행: sudo bash install.sh [관리자비밀번호]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "root 권한 필요: sudo bash $0" >&2; exit 1
fi

ADMIN_PW="${1:-admin1234}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Python 가상환경 생성 및 Flask 설치..."
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet flask openpyxl

echo "==> checkpass.sh 배포 (만료일/비활성화 지원)..."
install -m 0700 /home/user/openvpn-setup/server/checkpass.sh /etc/openvpn/server/checkpass.sh

echo "==> OpenVPN users 파일 확인..."
if [ ! -f /etc/openvpn/server/users ]; then
  install -m 0600 /home/user/openvpn-setup/server/users /etc/openvpn/server/users
  echo "  생성됨 (신규)"
else
  # admin@VPN → admin 마이그레이션
  if grep -q "^admin@VPN:" /etc/openvpn/server/users 2>/dev/null; then
    sed -i 's/^admin@VPN:/admin:/' /etc/openvpn/server/users
    echo "  admin@VPN → admin 마이그레이션 완료"
  fi
  # admin 계정 없으면 추가
  if ! grep -q "^admin:" /etc/openvpn/server/users 2>/dev/null; then
    ADMIN_LINE=$(grep "^admin:" /home/user/openvpn-setup/server/users 2>/dev/null || true)
    [ -n "$ADMIN_LINE" ] && echo "$ADMIN_LINE" >> /etc/openvpn/server/users && echo "  admin 추가됨"
  fi
  echo "  기존 users 파일 유지 (기존 유저 보존)"
fi

echo "==> user_expiry 파일 생성 (없으면)..."
touch /etc/openvpn/server/user_expiry
chmod 0600 /etc/openvpn/server/user_expiry

echo "==> disabled_users 파일 생성 (없으면)..."
touch /etc/openvpn/server/disabled_users
chmod 0600 /etc/openvpn/server/disabled_users

echo "==> client-connect.sh 배포 (접속 1대 제한)..."
install -m 0700 /home/user/openvpn-setup/server/client-connect.sh \
    /etc/openvpn/server/client-connect.sh

echo "==> unlimited_users 파일 관리 (admin 무제한)..."
UNLIMITED=/etc/openvpn/server/unlimited_users
if [ ! -f "$UNLIMITED" ]; then
  printf 'admin\n' > "$UNLIMITED"
  chmod 0600 "$UNLIMITED"
  echo "  생성: $UNLIMITED"
else
  # admin@VPN → admin 마이그레이션
  sed -i 's/^admin@VPN$/admin/' "$UNLIMITED"
  # admin 없으면 추가
  if ! grep -qxF 'admin' "$UNLIMITED"; then
    printf 'admin\n' >> "$UNLIMITED"
    echo "  admin 추가됨"
  fi
fi

echo "==> OpenVPN 서버 설정 배포 (management 소켓 + 5초 status 포함)..."
install -m 0644 /home/user/openvpn-setup/server/tcp443.conf   /etc/openvpn/server/tcp443.conf
install -m 0644 /home/user/openvpn-setup/server/udp-1703.conf /etc/openvpn/server/udp-1703.conf

echo "==> OpenVPN CCD 디렉터리 생성..."
mkdir -p /etc/openvpn/server/ccd

echo "==> client-config-dir 설정 추가..."
for conf in /etc/openvpn/server/tcp443.conf /etc/openvpn/server/udp-1703.conf; do
  if [ -f "$conf" ] && ! grep -q "client-config-dir" "$conf"; then
    echo "client-config-dir /etc/openvpn/server/ccd" >> "$conf"
    echo "  추가됨: $conf"
  fi
done

echo "==> OpenVPN 서비스 재시작 (설정 적용)..."
systemctl restart openvpn-server@tcp443.service   || true
systemctl restart openvpn-server@udp-1703.service || true

echo "==> systemd 서비스 설치 및 재시작..."
sed "s/ADMIN_PASSWORD=admin1234/ADMIN_PASSWORD=${ADMIN_PW}/" \
    "$DIR/openvpn-admin.service" > /etc/systemd/system/openvpn-admin.service

install -m 0644 "$DIR/openvpn-download.service" /etc/systemd/system/openvpn-download.service

systemctl daemon-reload
systemctl restart openvpn-admin.service
systemctl enable openvpn-admin.service
systemctl restart openvpn-download.service
systemctl enable openvpn-download.service

echo ""
echo "=========================== 완료 ==========================="
echo "  상태: $(systemctl is-active openvpn-admin.service)"
echo "  URL:  http://$(hostname -I | awk '{print $1}'):8080"
echo "  관리자 비밀번호: ${ADMIN_PW}"
echo "============================================================"
