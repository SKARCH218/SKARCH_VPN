# SKARCH VPN

라즈베리파이에서 운영하는 자체 OpenVPN 서버 + 웹 관리 패널

---

## 구성 개요

```
openvpn-setup/   ← 서버 설치 스크립트 & 설정 파일
openvpn-admin/   ← 웹 관리 패널 (Flask)
```

| 서비스 | 프로토콜 | 포트 | 설명 |
|---|---|---|---|
| openvpn-server@tcp443 | TCP | 443 | HTTPS처럼 보여 방화벽 우회 가능 |
| openvpn-server@udp-1703 | UDP | 1703 | 일반 환경, 빠른 속도 |

| 웹 서비스 | 포트 | 설명 |
|---|---|---|
| 다운로드 페이지 | 8000 | 공개 — OVPN 파일 다운로드 + 계정 신청 |
| 관리자 패널 | 8080 | 로그인 필요 — 계정 관리, 서버 제어 |

---

## openvpn-setup

### 설치

```bash
sudo bash openvpn-setup/install.sh
```

재실행해도 안전합니다. 기존 유저 목록은 덮어쓰지 않습니다.

### 유저 관리 (CLI)

```bash
# 목록 확인
sudo bash openvpn-setup/scripts/manage-users.sh list

# 유저 추가
sudo bash openvpn-setup/scripts/manage-users.sh add <아이디> <비밀번호>

# 유저 삭제
sudo bash openvpn-setup/scripts/manage-users.sh del <아이디>

# 비밀번호 변경
sudo bash openvpn-setup/scripts/manage-users.sh passwd <아이디> <새비밀번호>
```

### 운영 명령

```bash
# 상태 확인
systemctl status openvpn-server@tcp443
systemctl status openvpn-server@udp-1703

# 실시간 로그
journalctl -u openvpn-server@tcp443 -f
journalctl -u openvpn-server@udp-1703 -f

# 접속자 확인
cat /run/openvpn-server/status-tcp443.log
cat /run/openvpn-server/status-udp-1703.log
```

### 제거

```bash
sudo bash openvpn-setup/uninstall.sh           # 설정만 제거
sudo bash openvpn-setup/uninstall.sh --purge   # 패키지까지 제거
```

---

## openvpn-admin

Flask 기반 웹 관리 패널.

### 설치

```bash
cd openvpn-admin
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

서비스 등록:

```bash
sudo cp openvpn-admin.service /etc/systemd/system/
sudo cp openvpn-download.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openvpn-admin openvpn-download
```

### 기능

- **계정 신청 / 승인**: 사용자가 다운로드 페이지(8000)에서 이름·아이디·비밀번호·이메일 입력 → 관리자가 패널(8080)에서 승인 시 OpenVPN 계정 자동 생성 + 이메일 알림 발송
- **유저 관리**: 계정 생성 · 삭제 · 비밀번호 변경
- **서버 제어**: TCP 443 / UDP 1703 각각 시작 · 정지 · 재시작
- **이메일 알림**: Gmail SMTP (앱 비밀번호) 사용

### 이메일 설정

`openvpn-admin/data/email_config.json` 파일 생성:

```json
{
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_user": "your@gmail.com",
  "smtp_password": "앱비밀번호16자리",
  "smtp_from_name": "VPN 관리팀"
}
```

---

## 구성 요약

| 항목 | 내용 |
|---|---|
| 인증 방식 | 아이디 / 비밀번호 |
| 암호화 | AES-256-GCM |
| 라우팅 | 풀터널 (모든 트래픽 VPN 경유) |
| DNS | 1.1.1.1 / 8.8.8.8 |
| 자동 재부팅 | 매일 06:00 KST |
