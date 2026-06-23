#!/bin/bash
# OpenVPN auth-user-pass-verify handler (via-file method).
# $1 = path to a temp file: line 1 = username, line 2 = password.
# Exit 0 = accept, non-zero = reject.
# Credentials: /etc/openvpn/server/users  (username:$6$hash)
# Expiry:      /etc/openvpn/server/user_expiry  (username:YYYY-MM-DD)
set -euo pipefail

USERS_FILE="/etc/openvpn/server/users"
EXPIRY_FILE="/etc/openvpn/server/user_expiry"
DISABLED_FILE="/etc/openvpn/server/disabled_users"
CRED="${1:-}"

[ -n "$CRED" ] && [ -r "$CRED" ]   || exit 1
[ -r "$USERS_FILE" ]               || exit 1

user="$(sed -n '1p' "$CRED")"
pass="$(sed -n '2p' "$CRED")"
[ -n "$user" ] || exit 1

# 1) 비활성화 유저 거부
if [ -r "$DISABLED_FILE" ] && grep -qxF "$user" "$DISABLED_FILE"; then
  exit 1
fi

# 2) 비밀번호 검증
stored="$(awk -F: -v u="$user" '$1==u{print substr($0, length($1)+2); exit}' "$USERS_FILE")"
[ -n "$stored" ] || exit 1

salt="$(printf '%s' "$stored" | awk -F'$' '{print $3}')"
[ -n "$salt" ] || exit 1

calc="$(openssl passwd -6 -salt "$salt" "$pass")"
[ "$calc" = "$stored" ] || exit 1

# 3) 만료일 검증
if [ -r "$EXPIRY_FILE" ]; then
  expiry="$(awk -F: -v u="$user" '$1==u{print $2; exit}' "$EXPIRY_FILE")"
  if [ -n "$expiry" ]; then
    today="$(date +%Y-%m-%d)"
    # today > expiry 이면 만료 (YYYY-MM-DD 는 사전식 비교 가능)
    if [[ "$today" > "$expiry" ]]; then
      exit 1
    fi
  fi
fi

exit 0
