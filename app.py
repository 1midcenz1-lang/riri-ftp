import json
import os
import posixpath
import socket
import subprocess
import tempfile
import urllib.request
from urllib.parse import quote, urlparse, urlunparse
from base64 import b64decode
from datetime import datetime
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Dict, List

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

app = Flask(__name__)
AUTH_USERNAME = "midcenz"
AUTH_PASSWORD = "@Mani2244"


def check_auth() -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = b64decode(auth.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    username, password = decoded.split(":", 1)
    return username == AUTH_USERNAME and password == AUTH_PASSWORD


@app.before_request
def require_auth():
    if check_auth():
        return None
    return (
        jsonify({"ok": False, "error": "Authentication required"}),
        401,
        {"WWW-Authenticate": 'Basic realm="Riri FTP"'},
    )


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
                "base_https": raw.get(f"public_base_url{suffix}") or (f"http://{user}" if "." in user else f"http://{host}"),
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


def normalize_download_url(raw_url: str) -> str:
    clean = (raw_url or "").strip().replace("\\n", "").replace("\n", "").replace("\r", "")
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid URL. URL must start with http:// or https:// and contain a valid host.")
    host = parsed.hostname or ""
    try:
        host.encode("idna").decode("ascii")
    except Exception:
        raise ValueError("Invalid hostname in URL")
    safe_path = quote(parsed.path or "/", safe="/%._-~")
    safe_query = quote(parsed.query, safe="=&%._-~:/")
    return urlunparse((parsed.scheme, parsed.netloc, safe_path, parsed.params, safe_query, parsed.fragment))


def download_url_to_tempfile(file_url: str) -> str:
    last_error = "Unknown error"
    req = urllib.request.Request(
        file_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; RiriFTP/1.0)",
            "Accept": "*/*",
            "Connection": "close",
        },
    )
    for _ in range(3):
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_path = temp_file.name
        temp_file.close()
        try:
            with urllib.request.urlopen(req, timeout=120) as response, open(temp_path, "wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            return temp_path
        except Exception as e:
            last_error = str(e)
            if os.path.exists(temp_path):
                os.remove(temp_path)

    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_path = temp_file.name
    temp_file.close()
    curl_cmd = [
        "curl", "-fL", "--retry", "3", "--connect-timeout", "20", "--max-time", "600",
        "-A", "Mozilla/5.0 (compatible; RiriFTP/1.0)", "-o", temp_path, file_url
    ]
    try:
        subprocess.run(curl_cmd, check=True, capture_output=True, text=True)
        return temp_path
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise RuntimeError(f"{last_error} | curl fallback failed: {e}")


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


@app.route("/api/rename", methods=["POST"])
def api_rename():
    data = request.get_json(force=True)
    server_id = data.get("server_id", "")
    from_path = normalize_remote_path(data.get("from_path", "/"))
    new_name = data.get("new_name", "").strip()
    if not new_name or "/" in new_name:
        return jsonify({"ok": False, "error": "Invalid new name"}), 400
    destination = posixpath.join(posixpath.dirname(from_path), new_name)
    ftp = ftp_connect(server_id)
    try:
        ftp.rename(from_path, destination)
        return jsonify({"ok": True, "new_path": destination})
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
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                file.stream.seek(0)
                temp_file.write(file.stream.read())
                temp_path = temp_file.name
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to stage file {filename}: {e}"}), 400

        for attempt in range(1, retries + 1):
            ftp = None
            try:
                ftp = ftp_connect(server_id)
                ensure_remote_dir(ftp, target_dir)
                with open(temp_path, "rb") as src:
                    ftp.storbinary(f"STOR {filename}", src)
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
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

        if not success:
            return jsonify({"ok": False, "error": f"Upload failed for {filename}: {last_error}", "uploaded": uploaded}), 400

    return jsonify({"ok": True, "uploaded": uploaded})


@app.route("/api/upload-by-url", methods=["POST"])
def api_upload_by_url():
    data = request.get_json(force=True)
    server_id = data.get("server_id", "")
    target_dir = normalize_remote_path(data.get("target_dir", "/"))
    file_url = data.get("file_url", "")
    retries = max(1, min(5, int(data.get("retries", 2))))
    base_https = data.get("base_https", "").strip()
    if not file_url:
        return jsonify({"ok": False, "error": "file_url is required"}), 400
    try:
        normalized_url = normalize_download_url(file_url)
        filename = posixpath.basename(urlparse(normalized_url).path) or f"download-{int(datetime.utcnow().timestamp())}"
        temp_path = download_url_to_tempfile(normalized_url)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to download file: {e}"}), 400

    last_error = "Unknown error"
    uploaded = []
    success = False
    for attempt in range(1, retries + 1):
        ftp = None
        try:
            ftp = ftp_connect(server_id)
            ensure_remote_dir(ftp, target_dir)
            with open(temp_path, "rb") as src:
                ftp.storbinary(f"STOR {filename}", src)
            remote_path = posixpath.join(target_dir, filename) if target_dir != "/" else f"/{filename}"
            uploaded.append(
                {
                    "file": filename,
                    "remote_path": remote_path,
                    "download_link": (base_https.rstrip("/") + remote_path) if base_https else "",
                    "attempt": attempt,
                    "uploaded_at": datetime.utcnow().isoformat() + "Z",
                }
            )
            success = True
            break
        except Exception as e:
            last_error = str(e)
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except Exception:
                    pass

    if temp_path and os.path.exists(temp_path):
        os.remove(temp_path)
    if not success:
        return jsonify({"ok": False, "error": f"Upload failed for {filename}: {last_error}"}), 400
    return jsonify({"ok": True, "uploaded": uploaded})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "3001")))
