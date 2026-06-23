#!/bin/bash
# /etc/openvpn/server/client-connect.sh

TC=/usr/sbin/tc
PYTHON=/usr/bin/python3
STATUS_FILES=(
    "/run/openvpn-server/status-tcp443.log"
    "/run/openvpn-server/status-udp-1703.log"
)
UNLIMITED_FILE="/etc/openvpn/server/unlimited_users"
META_FILE="/home/user/openvpn-admin/data/users_meta.json"
LOG="/tmp/openvpn-connect.log"

USERNAME="${username:-}"
[ -z "$USERNAME" ] && exit 0

echo "$(date) CONNECT user=$USERNAME dev=$dev ip=$ifconfig_pool_remote_ip os=${IV_PLAT:-} gui=${IV_GUI_VER:-}" >> "$LOG"

# OS 정보 저장
OS_DIR="/run/openvpn-server/client-os"
mkdir -p "$OS_DIR"
echo "${IV_PLAT:-unknown}" > "$OS_DIR/$USERNAME"

# ── 1) 동시 접속 제한 ────────────────────────────────────────────────────
if ! { [ -f "$UNLIMITED_FILE" ] && grep -qxF "$USERNAME" "$UNLIMITED_FILE"; }; then
    for sf in "${STATUS_FILES[@]}"; do
        [ -r "$sf" ] || continue
        if awk -F',' -v u="$USERNAME" '$1=="CLIENT_LIST" && $2==u { found=1; exit } END { exit !found }' "$sf"; then
            echo "$(date) REJECT $USERNAME (already connected)" >> "$LOG"
            exit 1
        fi
    done
fi

# ── 2) 속도 제한 즉시 적용 (tc htb) ─────────────────────────────────────
DEV="${dev:-}"
CLIENT_IP="${ifconfig_pool_remote_ip:-}"

if [ -n "$DEV" ] && [ -n "$CLIENT_IP" ] && [ -x "$TC" ] && [ -f "$META_FILE" ]; then
    KBPS=$("$PYTHON" -c "
import json
try:
    d=json.load(open('$META_FILE'))
    print(int(d.get('$USERNAME',{}).get('speed_limit_kbps',0)))
except:
    print(0)
" 2>/dev/null)

    echo "$(date) SPEED user=$USERNAME kbps=${KBPS:-0} dev=$DEV ip=$CLIENT_IP" >> "$LOG"

    if [ "${KBPS:-0}" -gt 0 ] 2>/dev/null; then
        LAST=$(echo "$CLIENT_IP" | awk -F. '{print $NF}')
        CLASSID="1:${LAST}"
        PRIO="${LAST}"
        RATE="${KBPS}kbit"

        if ! "$TC" qdisc show dev "$DEV" 2>/dev/null | grep -q htb; then
            "$TC" qdisc replace dev "$DEV" root handle 1: htb default 99
            "$TC" class add dev "$DEV" parent 1: classid 1:99 htb rate 1gbit
        fi

        "$TC" class add dev "$DEV" parent 1: classid "$CLASSID" htb rate "$RATE" ceil "$RATE" 2>/dev/null || \
        "$TC" class change dev "$DEV" parent 1: classid "$CLASSID" htb rate "$RATE" ceil "$RATE"

        "$TC" filter del dev "$DEV" parent 1: prio "$PRIO" 2>/dev/null
        "$TC" filter add dev "$DEV" parent 1: prio "$PRIO" protocol ip u32 \
            match ip dst "${CLIENT_IP}/32" flowid "$CLASSID"

        echo "$(date) TC APPLIED $USERNAME @ $CLIENT_IP on $DEV rate=$RATE" >> "$LOG"
    fi
fi

exit 0
