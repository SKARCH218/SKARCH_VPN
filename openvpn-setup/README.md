# Raspberry Pi OpenVPN 서버

공인 IP `59.25.16.151` 기준.

## 활성 인스턴스

| 서비스 | 프로토콜 | 포트 | VPN 서브넷 | 용도 |
|---|---|---|---|---|
| `openvpn-server@udp-1703` | UDP | 1703 | 10.8.1.0/24 | 일반 (빠름) |
| `openvpn-server@tcp443`   | TCP | 443  | 10.8.2.0/24 | 방화벽 우회 (HTTPS처럼 보임) |

`openvpn-server@tcp-1703` 은 비활성 (부팅 시 시작 안 함).

## 설치 / 재설치

```bash
sudo bash /home/user/openvpn-setup/install.sh
```

재실행해도 안전합니다. `users` 파일(유저 목록)은 이미 존재하면 덮어쓰지 않습니다.

## 클라이언트 파일

```
client/client-tcp443.ovpn    ← TCP 443 (학교 방화벽 우회)
client/client-udp1703.ovpn   ← UDP 1703 (일반 환경, 빠름)
```

접속할 때 아이디/비밀번호를 입력하게 됩니다 (`auth-user-pass`).

## 공유기 포트포워딩

Pi 내부 IP `172.30.1.100` 기준:

| 프로토콜 | 외부 포트 | 내부 → |
|---|---|---|
| UDP | 1703 | 172.30.1.100:1703 |
| TCP | 443  | 172.30.1.100:443  |

## 유저 관리

```bash
# 목록 + 접속자 확인
sudo bash /home/user/openvpn-setup/scripts/manage-users.sh list

# 유저 추가
sudo bash /home/user/openvpn-setup/scripts/manage-users.sh add alice 비밀번호123

# 유저 삭제
sudo bash /home/user/openvpn-setup/scripts/manage-users.sh del alice

# 비밀번호 변경
sudo bash /home/user/openvpn-setup/scripts/manage-users.sh passwd alice 새비밀번호
```

변경 즉시 적용됩니다 (서버 재시작 불필요).

## 운영 명령

```bash
# 상태
systemctl status openvpn-server@tcp443
systemctl status openvpn-server@udp-1703

# 실시간 로그
journalctl -u openvpn-server@tcp443 -f
journalctl -u openvpn-server@udp-1703 -f

# 리스닝 포트 확인
ss -tulnp | grep -E ':(443|1703)'

# 접속자 목록
cat /run/openvpn-server/status-tcp443.log
cat /run/openvpn-server/status-udp-1703.log

# 다음 자동 재부팅
systemctl list-timers daily-reboot.timer
```

## 구성 요약

| 항목 | 내용 |
|---|---|
| 인증 | 아이디/비밀번호 (클라이언트 인증서 없음) |
| 암호화 | AES-256-GCM / AES-128-CBC (하위 호환) |
| 라우팅 | 풀터널 (모든 트래픽 VPN 경유) + DNS 1.1.1.1/8.8.8.8 |
| 자동 재부팅 | 매일 06:00 KST |
| SD 최적화 | journald volatile, status는 tmpfs, persist 파일 없음 |

## 제거

```bash
sudo bash /home/user/openvpn-setup/uninstall.sh          # 설정만 제거
sudo bash /home/user/openvpn-setup/uninstall.sh --purge  # 패키지까지 제거
```
