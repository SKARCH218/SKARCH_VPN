#!/bin/bash
# OpenVPN 유저 관리 스크립트
# 사용법:
#   sudo bash manage-users.sh add    <유저명> <비밀번호>
#   sudo bash manage-users.sh del    <유저명>
#   sudo bash manage-users.sh passwd <유저명> <새비밀번호>
#   sudo bash manage-users.sh list

set -euo pipefail

USERS_FILE="/etc/openvpn/server/users"

if [ "$(id -u)" -ne 0 ]; then
  echo "root 권한 필요:  sudo bash $0 $*" >&2
  exit 1
fi

CMD="${1:-}"
USER="${2:-}"
PASS="${3:-}"

case "$CMD" in

  add)
    [ -n "$USER" ] || { echo "사용법: $0 add <유저명> <비밀번호>" >&2; exit 1; }
    [ -n "$PASS" ] || { echo "사용법: $0 add <유저명> <비밀번호>" >&2; exit 1; }
    if grep -q "^${USER}:" "$USERS_FILE" 2>/dev/null; then
      echo "오류: 유저 '${USER}' 이미 존재합니다. passwd 명령으로 비밀번호를 변경하세요." >&2
      exit 1
    fi
    HASH="$(openssl passwd -6 "$PASS")"
    printf '%s:%s\n' "$USER" "$HASH" >> "$USERS_FILE"
    echo "유저 추가 완료: ${USER}"
    ;;

  del)
    [ -n "$USER" ] || { echo "사용법: $0 del <유저명>" >&2; exit 1; }
    if ! grep -q "^${USER}:" "$USERS_FILE" 2>/dev/null; then
      echo "오류: 유저 '${USER}' 를 찾을 수 없습니다." >&2
      exit 1
    fi
    sed -i "/^${USER}:/d" "$USERS_FILE"
    echo "유저 삭제 완료: ${USER}"
    ;;

  passwd)
    [ -n "$USER" ] || { echo "사용법: $0 passwd <유저명> <새비밀번호>" >&2; exit 1; }
    [ -n "$PASS" ] || { echo "사용법: $0 passwd <유저명> <새비밀번호>" >&2; exit 1; }
    if ! grep -q "^${USER}:" "$USERS_FILE" 2>/dev/null; then
      echo "오류: 유저 '${USER}' 를 찾을 수 없습니다." >&2
      exit 1
    fi
    HASH="$(openssl passwd -6 "$PASS")"
    sed -i "/^${USER}:/d" "$USERS_FILE"
    printf '%s:%s\n' "$USER" "$HASH" >> "$USERS_FILE"
    echo "비밀번호 변경 완료: ${USER}"
    ;;

  list)
    echo "=== 등록된 유저 목록 ==="
    if [ ! -f "$USERS_FILE" ] || [ ! -s "$USERS_FILE" ]; then
      echo "  (유저 없음)"
    else
      awk -F: '{print "  " $1}' "$USERS_FILE"
    fi
    echo ""
    echo "=== 현재 접속자 ==="
    for f in /run/openvpn-server/status-*.log; do
      [ -f "$f" ] || continue
      instance="$(basename "$f" .log | sed 's/^status-//')"
      count=$(awk '/^CLIENT_LIST,/{n++} END{print n+0}' "$f")
      printf "  %-15s %s명 접속 중\n" "$instance" "$count"
    done
    ;;

  *)
    echo "사용법:"
    echo "  sudo bash $0 add    <유저명> <비밀번호>   # 유저 추가"
    echo "  sudo bash $0 del    <유저명>              # 유저 삭제"
    echo "  sudo bash $0 passwd <유저명> <새비밀번호> # 비밀번호 변경"
    echo "  sudo bash $0 list                        # 유저 목록 + 접속자 확인"
    exit 1
    ;;
esac
