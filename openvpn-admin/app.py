#!/usr/bin/env python3
"""
OpenVPN 관리자 패널 (Flask)
실행: sudo python3 app.py  (또는 systemd 서비스로)
포트: 8080
"""
import json, os, re, secrets, socket as _socket, subprocess, threading, time
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from io import BytesIO
from flask import Flask, jsonify, redirect, render_template, request, send_file, session

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
META_FILE    = DATA_DIR / "users_meta.json"
OPENVPN_USERS  = Path("/etc/openvpn/server/users")
EXPIRY_FILE    = Path("/etc/openvpn/server/user_expiry")
DISABLED_FILE  = Path("/etc/openvpn/server/disabled_users")
CCD_DIR        = Path("/etc/openvpn/server/ccd")
STATUS_FILES  = {
    "tcp443":   "/run/openvpn-server/status-tcp443.log",
    "udp-1703": "/run/openvpn-server/status-udp-1703.log",
}
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")
MGMT_SOCKS = [
    "/run/openvpn-server/mgmt-tcp443.sock",
    "/run/openvpn-server/mgmt-udp1703.sock",
]

DATA_DIR.mkdir(parents=True, exist_ok=True)
REQUESTS_FILE = DATA_DIR / "requests.json"

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['TEMPLATES_AUTO_RELOAD'] = True  # 재시작 없이 템플릿 변경 반영

# ---------------------------------------------------------------------------
# 인메모리 현재 세션 (백그라운드 스레드가 갱신)
# ---------------------------------------------------------------------------
_current_sessions: dict = {}
_sessions_lock = threading.Lock()

# ---------------------------------------------------------------------------
# 헬퍼: 메타 DB
# ---------------------------------------------------------------------------
def load_meta() -> dict:
    if not META_FILE.exists():
        return {}
    with open(META_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_meta(data: dict):
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_requests() -> dict:
    if not REQUESTS_FILE.exists():
        return {}
    with open(REQUESTS_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_requests(data: dict):
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# 이메일 발송
# ---------------------------------------------------------------------------
EMAIL_CFG_FILE = DATA_DIR / "email_config.json"

def _load_email_cfg() -> dict:
    if not EMAIL_CFG_FILE.exists():
        return {}
    with open(EMAIL_CFG_FILE, encoding="utf-8") as f:
        return json.load(f)

def send_approval_email(to_email: str, display_name: str, username: str, password: str):
    cfg = _load_email_cfg()
    smtp_user = cfg.get("smtp_user", "").strip()
    if not smtp_user:
        return  # 이메일 미설정 시 건너뜀

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(cfg.get("smtp_port", 587))
    smtp_pw   = cfg.get("smtp_password", "")
    from_name = cfg.get("smtp_from_name", "SKARCH VPN")

    subject = "[SKARCH VPN] 계정 승인 완료"
    body = f"""안녕하세요, {display_name}님!

SKARCH VPN 계정이 승인되었습니다.

━━━━━━━━━━━━━━━━━━━━
아이디    : {username}
비밀번호  : {password}
━━━━━━━━━━━━━━━━━━━━

▶ 설정 파일 다운로드
   http://59.25.16.151:8000/

1. 위 주소에서 OVPN 파일을 다운로드하세요.
2. OpenVPN 앱에서 파일을 열고 위 계정으로 로그인하세요.

문의: https://open.kakao.com/o/sVH2hQAi (카카오톡 오픈채팅)

— SKARCH VPN 관리팀
"""
    msg = MIMEMultipart()
    msg["From"]    = f"{from_name} <{smtp_user}>"
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_user, smtp_pw)
            s.sendmail(smtp_user, to_email, msg.as_bytes())
    except Exception as e:
        print(f"[email error] {e}", flush=True)

# ---------------------------------------------------------------------------
# 헬퍼: 만료일 파일
# ---------------------------------------------------------------------------
def load_expiry() -> dict:
    """반환: {username: "YYYY-MM-DD"}"""
    result = {}
    if EXPIRY_FILE.exists():
        for line in EXPIRY_FILE.read_text().splitlines():
            line = line.strip()
            if ":" in line:
                u, d = line.split(":", 1)
                result[u.strip()] = d.strip()
    return result

def save_expiry(expiry: dict):
    lines = [f"{u}:{d}" for u, d in expiry.items() if d]
    try:
        EXPIRY_FILE.write_text("\n".join(lines) + ("\n" if lines else ""))
    except PermissionError:
        pass

def set_user_expiry(username: str, date_str: str | None):
    expiry = load_expiry()
    if date_str:
        expiry[username] = date_str
    else:
        expiry.pop(username, None)
    save_expiry(expiry)

def is_expired(date_str: str | None) -> bool:
    if not date_str:
        return False
    try:
        return date.today().isoformat() > date_str
    except Exception:
        return False

# ---------------------------------------------------------------------------
# 헬퍼: 비활성화 목록
# ---------------------------------------------------------------------------
def load_disabled() -> set:
    try:
        return set(line.strip() for line in DISABLED_FILE.read_text().splitlines() if line.strip())
    except Exception:
        return set()

def set_user_disabled(username: str, disabled: bool):
    current = load_disabled()
    if disabled:
        current.add(username)
    else:
        current.discard(username)
    DISABLED_FILE.write_text("\n".join(sorted(current)) + ("\n" if current else ""))

def kick_user(username: str):
    """현재 접속 중인 유저를 강제 연결 끊기.
    management 소켓 우선, 없으면 iptables 으로 차단."""
    kicked_via_mgmt = False
    for sock_path in MGMT_SOCKS:
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(sock_path)
            s.recv(4096)
            s.sendall(f"kill {username}\r\n".encode())
            s.recv(4096)
            s.close()
            kicked_via_mgmt = True
        except Exception:
            pass

    if kicked_via_mgmt:
        return

    # fallback: iptables 로 VPN IP 차단 → keepalive 타임아웃으로 자동 끊김
    with _sessions_lock:
        s = dict(_current_sessions).get(username, {})
    virt_addr = s.get("virt_addr", "")
    if virt_addr:
        for chain_flag, ip_flag in [("-s", virt_addr), ("-d", virt_addr)]:
            subprocess.run(
                ["iptables", "-I", "FORWARD", chain_flag, ip_flag, "-j", "DROP"],
                capture_output=True
            )
        # 재활성화 시 제거할 수 있게 meta에 저장
        try:
            meta = load_meta()
            meta.setdefault(username, {})["_blocked_ip"] = virt_addr
            save_meta(meta)
        except Exception:
            pass

def unkick_user(username: str):
    """iptables 차단 해제 (re-enable 시 호출)."""
    try:
        meta = load_meta()
        blocked_ip = meta.get(username, {}).pop("_blocked_ip", "")
        if blocked_ip:
            for chain_flag, ip_flag in [("-s", blocked_ip), ("-d", blocked_ip)]:
                subprocess.run(
                    ["iptables", "-D", "FORWARD", chain_flag, ip_flag, "-j", "DROP"],
                    capture_output=True
                )
            save_meta(meta)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 헬퍼: OpenVPN users 파일
# ---------------------------------------------------------------------------
def load_openvpn_users() -> list[str]:
    try:
        lines = OPENVPN_USERS.read_text().splitlines()
        return [l.split(":")[0] for l in lines if ":" in l]
    except Exception:
        return []

def openvpn_user_exists(username: str) -> bool:
    return username in load_openvpn_users()

def add_openvpn_user(username: str, pw_hash: str):
    with open(OPENVPN_USERS, "a") as f:
        f.write(f"{username}:{pw_hash}\n")

def remove_openvpn_user(username: str):
    lines = OPENVPN_USERS.read_text().splitlines(keepends=True)
    OPENVPN_USERS.write_text(
        "".join(l for l in lines if not l.startswith(f"{username}:"))
    )

def change_openvpn_password(username: str, pw_hash: str):
    lines = OPENVPN_USERS.read_text().splitlines(keepends=True)
    out = []
    for l in lines:
        out.append(f"{username}:{pw_hash}\n" if l.startswith(f"{username}:") else l)
    OPENVPN_USERS.write_text("".join(out))

# ---------------------------------------------------------------------------
# 헬퍼: 상태 파일 파싱
# ---------------------------------------------------------------------------
CLIENT_OS_DIR = Path("/run/openvpn-server/client-os")

def _get_client_os(username: str) -> str:
    try:
        return (CLIENT_OS_DIR / username).read_text().strip()
    except Exception:
        return ""

def parse_status_files() -> dict:
    """
    반환: {username: {bytes_upload, bytes_download, connections, instances,
                      real_address, connected_since_t, os}}
    status-version 2 컬럼 순서:
      CLIENT_LIST, cn, real_addr, virt_addr, virt_ipv6,
      bytes_recv(5), bytes_sent(6), conn_since(7), conn_since_t(8), username(9)
    """
    sessions: dict = {}
    for inst, path in STATUS_FILES.items():
        try:
            for line in Path(path).read_text().splitlines():
                if not line.startswith("CLIENT_LIST,"):
                    continue
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                cn          = parts[1]
                real_addr   = parts[2]
                # IPv6 필드가 parts[4]에 있으므로 bytes는 5, 6
                bytes_recv  = int(parts[5] or 0) if len(parts) > 5 else 0
                bytes_sent  = int(parts[6] or 0) if len(parts) > 6 else 0
                conn_since_t = int(parts[8] or 0) if len(parts) > 8 else 0
                virt_addr   = parts[3] if len(parts) > 3 else ""
                if cn not in sessions:
                    sessions[cn] = {
                        "bytes_upload": 0, "bytes_download": 0,
                        "connections": 0, "instances": [],
                        "real_address": real_addr,
                        "virt_addr": virt_addr,
                        "connected_since_t": conn_since_t,
                        "os": _get_client_os(cn),
                    }
                sessions[cn]["bytes_upload"]    += bytes_recv
                sessions[cn]["bytes_download"]  += bytes_sent
                sessions[cn]["connections"]     += 1
                if inst not in sessions[cn]["instances"]:
                    sessions[cn]["instances"].append(inst)
                # 가장 오래된 연결 시각 유지
                if conn_since_t and (
                    not sessions[cn]["connected_since_t"]
                    or conn_since_t < sessions[cn]["connected_since_t"]
                ):
                    sessions[cn]["connected_since_t"] = conn_since_t
                    sessions[cn]["real_address"] = real_addr
                    sessions[cn]["virt_addr"]    = virt_addr
        except Exception:
            pass
    return sessions

# ---------------------------------------------------------------------------
# 백그라운드 스레드: 누적 통계 갱신
# ---------------------------------------------------------------------------
def _stats_loop():
    global _current_sessions
    prev: dict = {}
    while True:
        try:
            cur  = parse_status_files()
            meta = load_meta()
            changed = False
            for u, s in prev.items():
                if u not in cur:
                    if u not in meta:
                        meta[u] = {}
                    meta[u]["acc_upload"]   = meta[u].get("acc_upload",   0) + s["bytes_upload"]
                    meta[u]["acc_download"] = meta[u].get("acc_download", 0) + s["bytes_download"]
                    changed = True
            if changed:
                save_meta(meta)
            with _sessions_lock:
                _current_sessions = cur
            prev = cur
        except Exception:
            pass
        time.sleep(15)  # 15초마다 갱신

threading.Thread(target=_stats_loop, daemon=True).start()

# admin 계정 메타 초기화 (admin@VPN → admin 마이그레이션 포함)
def _init_admin_meta():
    try:
        meta = load_meta()
        changed = False
        # 구 이름(admin@VPN) → 새 이름(admin) 마이그레이션
        if "admin@VPN" in meta and "admin" not in meta:
            meta["admin"] = meta.pop("admin@VPN")
            changed = True
        elif "admin@VPN" in meta:
            meta.pop("admin@VPN")
            changed = True
        entry = meta.setdefault("admin", {})
        if entry.get("display_name") != "관리자":
            entry["display_name"] = "관리자"
            changed = True
        if changed:
            save_meta(meta)
    except Exception:
        pass

_init_admin_meta()

# ---------------------------------------------------------------------------
# 헬퍼: tc 속도 제한 (즉시 적용, 재접속 불필요)
# ---------------------------------------------------------------------------
def _get_tun_for_ip(virt_ip: str) -> str:
    """VPN 가상 IP가 라우팅되는 tun 인터페이스 이름을 반환."""
    try:
        r = subprocess.run(["ip", "route", "get", virt_ip], capture_output=True, text=True)
        m = re.search(r'dev\s+(tun\d+)', r.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""

def _apply_tc(dev: str, virt_ip: str, kbps: int):
    """tc htb로 단일 클라이언트 egress 속도 제한. kbps=0 이면 제거."""
    last = int(virt_ip.split(".")[-1]) or 1
    classid = f"1:{last}"
    prio    = str(last)

    if kbps <= 0:
        subprocess.run(["tc", "filter", "del", "dev", dev, "parent", "1:", "prio", prio],
                       capture_output=True)
        subprocess.run(["tc", "class",  "del", "dev", dev, "parent", "1:", "classid", classid],
                       capture_output=True)
        return

    # root htb qdisc 없으면 교체
    r = subprocess.run(["tc", "qdisc", "show", "dev", dev], capture_output=True, text=True)
    if "htb" not in r.stdout:
        subprocess.run(["tc", "qdisc", "replace", "dev", dev, "root",
                        "handle", "1:", "htb", "default", "99"], capture_output=True)
        subprocess.run(["tc", "class", "add", "dev", dev, "parent", "1:",
                        "classid", "1:99", "htb", "rate", "1gbit"], capture_output=True)

    rate = f"{kbps}kbit"
    r = subprocess.run(["tc", "class", "add", "dev", dev, "parent", "1:",
                        "classid", classid, "htb", "rate", rate, "ceil", rate],
                       capture_output=True)
    if r.returncode != 0:
        subprocess.run(["tc", "class", "change", "dev", dev, "parent", "1:",
                        "classid", classid, "htb", "rate", rate, "ceil", rate],
                       capture_output=True)

    subprocess.run(["tc", "filter", "del", "dev", dev, "parent", "1:", "prio", prio],
                   capture_output=True)
    subprocess.run(["tc", "filter", "add", "dev", dev, "parent", "1:", "prio", prio,
                    "protocol", "ip", "u32",
                    "match", "ip", "dst", f"{virt_ip}/32", "flowid", classid],
                   capture_output=True)

# ---------------------------------------------------------------------------
# 헬퍼: 속도 제한 (CCD shaper + tc 즉시 적용)
# ---------------------------------------------------------------------------
def apply_speed_limit(username: str, kbps: int):
    # CCD shaper: 다음 접속부터 적용 (재접속 필요)
    CCD_DIR.mkdir(parents=True, exist_ok=True)
    ccd = CCD_DIR / username
    if kbps > 0:
        bps = kbps * 1024 // 8
        ccd.write_text(f"shaper {bps}\n")
    else:
        if ccd.exists():
            ccd.unlink()

    # tc: 현재 접속 중인 경우 즉시 적용
    with _sessions_lock:
        s = dict(_current_sessions).get(username, {})
    virt_addr = s.get("virt_addr", "")
    if virt_addr:
        dev = _get_tun_for_ip(virt_addr)
        if dev:
            try:
                _apply_tc(dev, virt_addr, kbps)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# 헬퍼: 패스워드
# ---------------------------------------------------------------------------
def gen_password(length: int = 10) -> str:
    chars = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(chars) for _ in range(length))

def hash_password(password: str) -> str:
    r = subprocess.run(["openssl", "passwd", "-6", password], capture_output=True, text=True)
    return r.stdout.strip()

# ---------------------------------------------------------------------------
# 인증 데코레이터
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# 인증 라우트
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json() or {}
        if data.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return jsonify({"ok": True})
        return jsonify({"error": "비밀번호가 틀렸습니다"}), 401
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect("/login")
    return render_template("index.html")

# ---------------------------------------------------------------------------
# API: 실시간 세션 (10초 폴링용 경량 엔드포인트)
# ---------------------------------------------------------------------------
@app.route("/api/sessions")
@login_required
def api_sessions():
    with _sessions_lock:
        return jsonify(dict(_current_sessions))

# ---------------------------------------------------------------------------
# API: 대시보드 통계
# ---------------------------------------------------------------------------
@app.route("/api/stats")
@login_required
def api_stats():
    meta   = load_meta()
    expiry = load_expiry()
    with _sessions_lock:
        sessions = dict(_current_sessions)

    total_upload   = sum(u.get("acc_upload",   0) for u in meta.values())
    total_download = sum(u.get("acc_download", 0) for u in meta.values())
    for s in sessions.values():
        total_upload   += s["bytes_upload"]
        total_download += s["bytes_download"]

    today = date.today().isoformat()
    expired_count = sum(1 for u in load_openvpn_users() if is_expired(expiry.get(u)))

    return jsonify({
        "total_users":    len(load_openvpn_users()),
        "online_users":   len(sessions),
        "total_upload":   total_upload,
        "total_download": total_download,
        "expired_users":  expired_count,
    })

# ---------------------------------------------------------------------------
# API: 유저 목록
# ---------------------------------------------------------------------------
@app.route("/api/users")
@login_required
def api_users():
    meta     = load_meta()
    expiry   = load_expiry()
    disabled = load_disabled()
    with _sessions_lock:
        sessions = dict(_current_sessions)

    result = []
    for username in load_openvpn_users():
        m   = meta.get(username, {})
        s   = sessions.get(username, {})
        exp = expiry.get(username)
        result.append({
            "username":          username,
            "display_name":      m.get("display_name", username),
            "speed_limit_kbps":  m.get("speed_limit_kbps", 0),
            "online":            username in sessions,
            "connections":       s.get("connections", 0),
            "instances":         s.get("instances", []),
            "real_address":      s.get("real_address", ""),
            "connected_since_t": s.get("connected_since_t", 0),
            "bytes_upload":      m.get("acc_upload",   0) + s.get("bytes_upload",   0),
            "bytes_download":    m.get("acc_download", 0) + s.get("bytes_download", 0),
            "created_at":        m.get("created_at", ""),
            "expires_at":        exp or "",
            "is_expired":        is_expired(exp),
            "enabled":           username not in disabled,
            "os":                s.get("os", ""),
        })

    return jsonify(result)

# ---------------------------------------------------------------------------
# API: 유저 추가
# ---------------------------------------------------------------------------
@app.route("/api/users", methods=["POST"])
@login_required
def api_add_user():
    data             = request.get_json() or {}
    display_name     = data.get("display_name", "").strip()
    username         = data.get("username", "").strip()
    speed_limit_kbps = int(data.get("speed_limit_kbps", 0))
    expires_at       = data.get("expires_at", "").strip()

    if not display_name:
        return jsonify({"error": "별명을 입력하세요"}), 400

    if not username:
        base     = re.sub(r"[^a-z0-9_]", "", display_name.lower().replace(" ", "_"))
        username = base or f"user_{secrets.token_hex(3)}"

    if openvpn_user_exists(username):
        return jsonify({"error": f"'{username}' 이미 존재합니다"}), 409

    password = gen_password()
    pw_hash  = hash_password(password)

    try:
        add_openvpn_user(username, pw_hash)
    except PermissionError:
        return jsonify({"error": "users 파일 쓰기 권한 없음 (root 실행 필요)"}), 500

    meta = load_meta()
    meta[username] = {
        "display_name":     display_name,
        "speed_limit_kbps": speed_limit_kbps,
        "acc_upload":       0,
        "acc_download":     0,
        "created_at":       datetime.now().isoformat(timespec="seconds"),
        "password":         password,
    }
    save_meta(meta)

    if expires_at:
        try:
            set_user_expiry(username, expires_at)
        except Exception:
            pass

    if speed_limit_kbps > 0:
        try:
            apply_speed_limit(username, speed_limit_kbps)
        except Exception:
            pass

    return jsonify({"username": username, "password": password, "display_name": display_name})

# ---------------------------------------------------------------------------
# API: 유저 수정
# ---------------------------------------------------------------------------
@app.route("/api/users/<username>", methods=["PATCH"])
@login_required
def api_update_user(username: str):
    data = request.get_json() or {}
    meta = load_meta()
    if username not in meta:
        meta[username] = {}

    if "display_name" in data:
        meta[username]["display_name"] = data["display_name"].strip()

    if "speed_limit_kbps" in data:
        kbps = int(data["speed_limit_kbps"])
        meta[username]["speed_limit_kbps"] = kbps
        try:
            apply_speed_limit(username, kbps)
        except PermissionError:
            return jsonify({"error": "CCD 디렉토리 쓰기 권한 없음"}), 500

    if "expires_at" in data:
        try:
            set_user_expiry(username, data["expires_at"] or None)
        except Exception:
            pass

    if "enabled" in data:
        try:
            set_user_disabled(username, not data["enabled"])
            if not data["enabled"]:
                kick_user(username)
            else:
                unkick_user(username)
        except PermissionError:
            return jsonify({"error": "disabled_users 파일 쓰기 권한 없음"}), 500

    save_meta(meta)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API: 유저 삭제
# ---------------------------------------------------------------------------
@app.route("/api/users/<username>", methods=["DELETE"])
@login_required
def api_delete_user(username: str):
    try:
        remove_openvpn_user(username)
    except PermissionError:
        return jsonify({"error": "권한 없음"}), 500

    ccd = CCD_DIR / username
    try:
        if ccd.exists():
            ccd.unlink()
    except Exception:
        pass

    try:
        set_user_expiry(username, None)
    except Exception:
        pass

    meta = load_meta()
    meta.pop(username, None)
    save_meta(meta)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API: 비밀번호 변경 (수동 입력 or 자동 생성)
# ---------------------------------------------------------------------------
@app.route("/api/users/<username>", methods=["GET"])
@login_required
def api_get_user(username: str):
    meta = load_meta()
    if username not in meta:
        return jsonify({"error": "없는 사용자"}), 404
    return jsonify({"username": username, "password": meta[username].get("password", "")})

@app.route("/api/users/<username>/reset-password", methods=["POST"])
@login_required
def api_reset_password(username: str):
    data      = request.get_json() or {}
    manual_pw = data.get("password", "").strip()
    password  = manual_pw if manual_pw else gen_password()
    pw_hash   = hash_password(password)
    try:
        change_openvpn_password(username, pw_hash)
    except PermissionError:
        return jsonify({"error": "권한 없음"}), 500
    meta = load_meta()
    if username in meta:
        meta[username]["password"] = password
        save_meta(meta)
    return jsonify({"password": password})

# ---------------------------------------------------------------------------
# API: 서버 제어
# ---------------------------------------------------------------------------
VPN_SERVICES = ["openvpn-server@tcp443.service", "openvpn-server@udp-1703.service"]
SVC_MAP = {
    "tcp": "openvpn-server@tcp443.service",
    "udp": "openvpn-server@udp-1703.service",
}

def _svc_active(name: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", name], capture_output=True)
    return r.returncode == 0

@app.route("/api/server/status")
@login_required
def api_server_status():
    return jsonify({
        "tcp443":   _svc_active("openvpn-server@tcp443.service"),
        "udp1703":  _svc_active("openvpn-server@udp-1703.service"),
        "vpn_up":   _svc_active("openvpn-server@tcp443.service") or
                    _svc_active("openvpn-server@udp-1703.service"),
    })

@app.route("/api/server/restart-vpn", methods=["POST"])
@login_required
def api_restart_vpn():
    for svc in VPN_SERVICES:
        subprocess.run(["systemctl", "restart", svc], capture_output=True)
    return jsonify({"ok": True})

@app.route("/api/server/toggle-vpn", methods=["POST"])
@login_required
def api_toggle_vpn():
    data    = request.get_json() or {}
    enable  = data.get("enable", True)
    action  = "start" if enable else "stop"
    for svc in VPN_SERVICES:
        subprocess.run(["systemctl", action, svc], capture_output=True)
    return jsonify({"ok": True, "vpn_up": enable})

@app.route("/api/server/reboot", methods=["POST"])
@login_required
def api_reboot():
    subprocess.Popen(["bash", "-c", "sleep 1 && systemctl reboot"])
    return jsonify({"ok": True})

@app.route("/api/server/<svc>/toggle", methods=["POST"])
@login_required
def api_toggle_svc(svc: str):
    if svc not in SVC_MAP:
        return jsonify({"error": "알 수 없는 서비스"}), 400
    data   = request.get_json() or {}
    enable = data.get("enable", True)
    subprocess.run(["systemctl", "start" if enable else "stop", SVC_MAP[svc]], capture_output=True)
    return jsonify({"ok": True})

@app.route("/api/server/<svc>/restart", methods=["POST"])
@login_required
def api_restart_svc(svc: str):
    if svc not in SVC_MAP:
        return jsonify({"error": "알 수 없는 서비스"}), 400
    subprocess.run(["systemctl", "restart", SVC_MAP[svc]], capture_output=True)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API: 유저 목록 Excel 내보내기
# ---------------------------------------------------------------------------
@app.route("/api/users/export")
@login_required
def api_export_users():
    import csv, io
    meta   = load_meta()
    expiry = load_expiry()
    users  = load_openvpn_users()

    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["표시이름", "유저이름", "비밀번호", "속도제한(Kbps)", "만료일"])
    for username in users:
        m   = meta.get(username, {})
        spd = m.get("speed_limit_kbps", 0)
        w.writerow([
            m.get("display_name", username),
            username,
            "",                          # 비밀번호는 해시 저장이라 내보내기 불가
            spd if spd else "",
            expiry.get(username, ""),
        ])

    # BOM 포함 → 한글 깨짐 없이 Excel에서 바로 열림
    content = "﻿" + buf.getvalue()
    return send_file(
        BytesIO(content.encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="vpn_users.csv",
    )

# ---------------------------------------------------------------------------
# API: Excel 파일로 유저 일괄 추가
# ---------------------------------------------------------------------------
@app.route("/api/users/import", methods=["POST"])
@login_required
def api_import_users():
    if "file" not in request.files:
        return jsonify({"error": "파일이 없습니다"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        return jsonify({"error": ".xlsx / .xls / .csv 파일만 지원합니다"}), 400

    created, skipped, errors = [], [], []

    try:
        if f.filename.lower().endswith(".csv"):
            import csv, io as _io
            text   = f.read().decode("utf-8-sig")
            reader = csv.reader(_io.StringIO(text))
            rows   = list(reader)[1:]   # 헤더 제외
        else:
            import openpyxl as _openpyxl
            wb   = _openpyxl.load_workbook(BytesIO(f.read()), data_only=True)
            ws   = wb.active
            rows = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
    except Exception as e:
        return jsonify({"error": f"파일 파싱 오류: {e}"}), 400

    meta = load_meta()

    for i, row in enumerate(rows, 2):
        if not row or not any(row):
            continue

        def _str(v): return str(v).strip() if v is not None else ""

        display_name = _str(row[0] if len(row) > 0 else "")
        username     = _str(row[1] if len(row) > 1 else "")
        password     = _str(row[2] if len(row) > 2 else "")
        speed_raw    = row[3] if len(row) > 3 else None
        expires_at   = _str(row[4] if len(row) > 4 else "")

        if not display_name:
            errors.append(f"행 {i}: 표시이름 없음")
            continue

        if not username:
            base     = re.sub(r"[^a-z0-9_]", "", display_name.lower().replace(" ", "_"))
            username = base or f"user_{secrets.token_hex(3)}"

        if openvpn_user_exists(username):
            skipped.append(username)
            continue

        try:
            speed_kbps = int(float(speed_raw)) if speed_raw not in (None, "") else 0
        except Exception:
            speed_kbps = 0

        if not password:
            password = gen_password()

        pw_hash = hash_password(password)
        try:
            add_openvpn_user(username, pw_hash)
        except PermissionError:
            errors.append(f"{username}: 쓰기 권한 없음")
            continue

        meta[username] = {
            "display_name":     display_name,
            "speed_limit_kbps": speed_kbps,
            "acc_upload":       0,
            "acc_download":     0,
            "created_at":       datetime.now().isoformat(timespec="seconds"),
        }
        if expires_at:
            try:
                set_user_expiry(username, expires_at)
            except Exception:
                pass
        if speed_kbps > 0:
            try:
                apply_speed_limit(username, speed_kbps)
            except Exception:
                pass

        created.append({"username": username, "display_name": display_name, "password": password})

    save_meta(meta)
    return jsonify({"created": created, "skipped": skipped, "errors": errors})

# ---------------------------------------------------------------------------
# API: 회원가입 신청 관리
# ---------------------------------------------------------------------------
@app.route("/api/requests")
@login_required
def api_get_requests():
    reqs    = load_requests()
    pending = [{"id": k, **v} for k, v in reqs.items() if v.get("status") == "pending"]
    pending.sort(key=lambda x: x["requested_at"])
    return jsonify(pending)

@app.route("/api/requests/<req_id>/approve", methods=["POST"])
@login_required
def api_approve_request(req_id: str):
    reqs = load_requests()
    if req_id not in reqs:
        return jsonify({"error": "요청을 찾을 수 없습니다"}), 404

    req          = reqs[req_id]
    display_name = req["display_name"]
    username     = req.get("username", "").strip()
    password     = req.get("password", "").strip()
    email        = req.get("email", "").strip()

    # 하위 호환: 구버전 신청(username 없음)은 자동 생성
    if not username:
        base     = re.sub(r"[^a-z0-9_]", "", display_name.lower().replace(" ", "_"))
        username = base or f"user_{secrets.token_hex(3)}"
        suffix, orig = 1, username
        while openvpn_user_exists(username):
            username = f"{orig}_{suffix}"; suffix += 1
    if not password:
        password = gen_password()

    if openvpn_user_exists(username):
        return jsonify({"error": f"'{username}' 아이디가 이미 존재합니다"}), 409

    pw_hash = hash_password(password)
    try:
        add_openvpn_user(username, pw_hash)
    except PermissionError:
        return jsonify({"error": "users 파일 쓰기 권한 없음"}), 500

    meta = load_meta()
    meta[username] = {
        "display_name":     display_name,
        "speed_limit_kbps": 0,
        "acc_upload":       0,
        "acc_download":     0,
        "created_at":       datetime.now().isoformat(timespec="seconds"),
        "password":         password,
    }
    save_meta(meta)

    reqs[req_id]["status"] = "approved"
    save_requests(reqs)

    # 이메일 발송 (설정된 경우에만)
    if email:
        threading.Thread(
            target=send_approval_email,
            args=(email, display_name, username, password),
            daemon=True,
        ).start()

    return jsonify({
        "username":     username,
        "password":     password,
        "display_name": display_name,
        "email":        email,
    })

@app.route("/api/requests/<req_id>/reject", methods=["POST"])
@login_required
def api_reject_request(req_id: str):
    reqs = load_requests()
    if req_id not in reqs:
        return jsonify({"error": "요청을 찾을 수 없습니다"}), 404
    reqs[req_id]["status"] = "rejected"
    save_requests(reqs)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
