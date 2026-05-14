import json
import os
import posixpath
import socket
from datetime import datetime
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Dict, List

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

app = Flask(__name__)


def load_servers() -> List[Dict[str, str]]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    servers = []
    index = 1
    while True:
        suffix = "" if index == 1 else str(index)
        host = raw.get(f"ftp_host{suffix}")
        user = raw.get(f"ftp_user{suffix}")
        password = raw.get(f"ftp_password{suffix}")
        if not host or not user or not password:
            if index == 1:
                raise ValueError("No FTP servers found in config.json")
            break
        servers.append(
            {
                "id": f"srv-{index}",
                "name": f"Server {index} ({host})",
                "host": host,
                "user": user,
                "password": password,
                "base_https": f"https://{host}",
            }
        )
        index += 1

    return servers


SERVERS = load_servers()
SERVER_MAP = {s["id"]: s for s in SERVERS}


def ftp_connect(server_id: str) -> FTP:
    server = SERVER_MAP.get(server_id)
    if not server:
        raise ValueError("Invalid server id")

    ftp = FTP()
    ftp.connect(server["host"], 21, timeout=20)
    ftp.login(server["user"], server["password"])
    ftp.set_pasv(True)
    return ftp


def normalize_remote_path(path: str) -> str:
    p = path.strip() if path else "/"
    if not p.startswith("/"):
        p = "/" + p
    return posixpath.normpath(p)


def ensure_remote_dir(ftp: FTP, directory: str):
    directory = normalize_remote_path(directory)
    if directory in ("", "/", "."):
        ftp.cwd("/")
        return

    parts = [part for part in directory.split("/") if part]
    ftp.cwd("/")
    for part in parts:
        try:
            ftp.cwd(part)
        except error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def list_remote(ftp: FTP, path: str):
    path = normalize_remote_path(path)
    entries = []

    try:
        ftp.cwd(path)
    except error_perm:
        raise FileNotFoundError(f"Directory not found: {path}")

    try:
        lines = []
        ftp.retrlines("LIST", lines.append)
        for line in lines:
            chunks = line.split(maxsplit=8)
            if len(chunks) < 9:
                continue
            is_dir = chunks[0].startswith("d")
            size = chunks[4]
            name = chunks[8]
            item_path = posixpath.join(path, name) if path != "/" else f"/{name}"
            entries.append(
                {
                    "name": name,
                    "type": "dir" if is_dir else "file",
                    "size": int(size) if size.isdigit() else 0,
                    "path": item_path,
                }
            )
    finally:
        ftp.cwd("/")

    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return entries


@app.route("/")
def index():
    public_servers = [
        {
            "id": s["id"],
            "name": s["name"],
            "host": s["host"],
            "base_https": s["base_https"],
        }
        for s in SERVERS
    ]
    return render_template("index.html", servers=public_servers)


@app.route("/api/list")
def api_list():
    server_id = request.args.get("server_id", "")
    path = request.args.get("path", "/")
    ftp = ftp_connect(server_id)
    try:
        items = list_remote(ftp, path)
        return jsonify({"ok": True, "path": normalize_remote_path(path), "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        ftp.quit()


@app.route("/api/mkdir", methods=["POST"])
def api_mkdir():
    data = request.get_json(force=True)
    server_id = data.get("server_id", "")
    path = normalize_remote_path(data.get("path", "/"))
    folder_name = data.get("folder_name", "").strip()

    if not folder_name:
        return jsonify({"ok": False, "error": "folder_name is required"}), 400

    ftp = ftp_connect(server_id)
    try:
        ensure_remote_dir(ftp, path)
        ftp.mkd(folder_name)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        ftp.quit()


@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.get_json(force=True)
    server_id = data.get("server_id", "")
    target_path = normalize_remote_path(data.get("target_path", "/"))
    item_type = data.get("type", "file")

    ftp = ftp_connect(server_id)
    try:
        if item_type == "dir":
            def remove_dir_recursive(p: str):
                items = list_remote(ftp, p)
                for item in items:
                    if item["type"] == "dir":
                        remove_dir_recursive(item["path"])
                    else:
                        ftp.delete(item["path"])
                ftp.rmd(p)

            remove_dir_recursive(target_path)
        else:
            ftp.delete(target_path)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        ftp.quit()


@app.route("/api/upload", methods=["POST"])
def api_upload():
    server_id = request.form.get("server_id", "")
    target_dir = normalize_remote_path(request.form.get("target_dir", "/"))
    retries = max(1, min(5, int(request.form.get("retries", "2"))))
    base_https = request.form.get("base_https", "").strip()

    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "No files uploaded"}), 400

    uploaded = []
    for file in files:
        filename = file.filename
        if not filename:
            continue

        success = False
        last_error = "Unknown error"

        for attempt in range(1, retries + 1):
            ftp = None
            try:
                ftp = ftp_connect(server_id)
                ensure_remote_dir(ftp, target_dir)
                file.stream.seek(0)
                ftp.storbinary(f"STOR {filename}", file.stream)
                remote_path = posixpath.join(target_dir, filename) if target_dir != "/" else f"/{filename}"
                download_link = (base_https.rstrip("/") + remote_path) if base_https else ""
                uploaded.append(
                    {
                        "file": filename,
                        "remote_path": remote_path,
                        "download_link": download_link,
                        "attempt": attempt,
                        "uploaded_at": datetime.utcnow().isoformat() + "Z",
                    }
                )
                success = True
                break
            except (socket.timeout, ConnectionError, OSError, error_perm) as e:
                last_error = str(e)
            finally:
                if ftp is not None:
                    try:
                        ftp.quit()
                    except Exception:
                        pass

        if not success:
            return jsonify({"ok": False, "error": f"Upload failed for {filename}: {last_error}", "uploaded": uploaded}), 400

    return jsonify({"ok": True, "uploaded": uploaded})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "3001")))
