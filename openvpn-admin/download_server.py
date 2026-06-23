#!/usr/bin/env python3
"""SKARCH VPN 다운로드 페이지 — 포트 8000 (공개, 로그인 불필요)"""
import json, secrets
from datetime import datetime
from flask import Flask, render_template, send_file, request, jsonify
from pathlib import Path

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

CLIENT_DIR    = Path("/home/user/openvpn-setup/client")
DATA_DIR      = Path("/home/user/openvpn-admin/data")
REQUESTS_FILE = DATA_DIR / "requests.json"

FILES = {
    "tcp": CLIENT_DIR / "SKARCH-TCP-443.ovpn",
    "udp": CLIENT_DIR / "SKARCH-UDP-1703.ovpn",
}

def _load_reqs() -> dict:
    if not REQUESTS_FILE.exists():
        return {}
    with open(REQUESTS_FILE, encoding="utf-8") as f:
        return json.load(f)

def _save_reqs(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

@app.route("/")
def index():
    return render_template("download.html")

@app.route("/download/<proto>")
def download(proto):
    if proto not in FILES:
        return "Not found", 404
    f = FILES[proto]
    if not f.exists():
        return "파일이 없습니다", 404
    return send_file(f, as_attachment=True, download_name=f.name,
                     mimetype="application/x-openvpn-profile")

@app.route("/request", methods=["POST"])
def submit_request():
    import re as _re
    data         = request.get_json() or {}
    display_name = data.get("display_name", "").strip()
    username     = data.get("username", "").strip().lower()
    password     = data.get("password", "")
    email        = data.get("email", "").strip()

    if not display_name:
        return jsonify({"error": "이름을 입력하세요"}), 400
    if not username:
        return jsonify({"error": "아이디를 입력하세요"}), 400
    if not _re.fullmatch(r"[a-z0-9_]{3,20}", username):
        return jsonify({"error": "아이디는 영문 소문자·숫자·_만 사용 가능 (3~20자)"}), 400
    if len(password) < 6:
        return jsonify({"error": "비밀번호는 6자 이상이어야 합니다"}), 400
    if not email or not _re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return jsonify({"error": "올바른 이메일 주소를 입력하세요"}), 400

    # 이미 신청 대기 중인 아이디 중복 체크
    reqs = _load_reqs()
    for v in reqs.values():
        if v.get("username") == username and v.get("status") == "pending":
            return jsonify({"error": f"'{username}' 아이디는 이미 신청 중입니다"}), 409

    req_id = secrets.token_hex(8)
    reqs[req_id] = {
        "display_name": display_name,
        "username":     username,
        "password":     password,
        "email":        email,
        "requested_at": datetime.now().isoformat(timespec="seconds"),
        "status":       "pending",
    }
    _save_reqs(reqs)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
