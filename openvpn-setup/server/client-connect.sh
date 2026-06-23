#!/bin/bash
# /etc/openvpn/server/client-connect.sh
# 접속 시 OpenVPN이 호출. exit 0 = 허용, exit 1 = 거부.
#
# 동작:
#   1) 유저당 동시 접속 1대 제한 (unlimited_users 예외)
#   2) 메타 파일에 speed_limit_kbps 설정 시 tc htb로 즉시 적용

STATUS_FILES=(
    "/run/openvpn-server/status-tcp443.log"
    "/run/openvpn-server/status-udp-1703.log"
)
UNLIMITED_FILE="/etc/openvpn/server/unlimited_users"
META_FILE="/home/user/openvpn-admin/data/users_meta.json"

USERNAME="${username:-}"
[ -z "$USERNAME" ] && exit 0

# ── 1) 동시 접속 제한 ────────────────────────────────────────────────────
if ! { [ -f "$UNLIMITED_FILE" ] && grep -qxF "$USERNAME" "$UNLIMITED_FILE"; }; then
    for sf in "${STATUS_FILES[@]}"; do
        [ -r "$sf" ] || continue
        if awk -F',' -v u="$USERNAME" '$1=="CLIENT_LIST" && $2==u { found=1; exit } END { exit !found }' "$sf"; then
            exit 1   # 거부
        fi
    done
fi

# ── 2) 속도 제한 즉시 적용 (tc htb) ─────────────────────────────────────
# OpenVPN 환경변수: $dev (tun 인터페이스), $ifconfig_pool_remote_ip (클라이언트 VPN IP)
DEV="${dev:-}"
CLIENT_IP="${ifconfig_pool_remote_ip:-}"

if [ -n "$DEV" ] && [ -n "$CLIENT_IP" ] && command -v tc &>/dev/null && [ -f "$META_FILE" ]; then
    KBPS=$(python3 -c "
import json,sys
try:
    d=json.load(open('$META_FILE'))
    print(int(d.get('$USERNAME',{}).get('speed_limit_kbps',0)))
except:
    print(0)
" 2>/dev/null)

    if [ "${KBPS:-0}" -gt 0 ] 2>/dev/null; then
        LAST=$(echo "$CLIENT_IP" | awk -F. '{print $NF}')
        CLASSID="1:${LAST}"
        PRIO="${LAST}"
        RATE="${KBPS}kbit"

        # root htb qdisc 없으면 생성
        if ! tc qdisc show dev "$DEV" 2>/dev/null | grep -q htb; then
            tc qdisc replace dev "$DEV" root handle 1: htb default 99
            tc class add dev "$DEV" parent 1: classid 1:99 htb rate 1gbit
        fi

        # 클라이언트 클래스 추가/갱신
        tc class add dev "$DEV" parent 1: classid "$CLASSID" htb rate "$RATE" ceil "$RATE" 2>/dev/null || \
        tc class change dev "$DEV" parent 1: classid "$CLASSID" htb rate "$RATE" ceil "$RATE"

        # 필터 교체
        tc filter del dev "$DEV" parent 1: prio "$PRIO" 2>/dev/null
        tc filter add dev "$DEV" parent 1: prio "$PRIO" protocol ip u32 \
            match ip dst "${CLIENT_IP}/32" flowid "$CLASSID"
    fi
fi

exit 0
