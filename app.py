import json
import os
import posixpath
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from urllib.parse import quote, urlparse, urlunparse
from base64 import b64decode
from datetime import datetime
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Dict, List
from urllib.parse import unquote


class TaskCancelled(Exception):
    pass

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOCAL_STORE_DIR = BASE_DIR / "local_store"
LOCAL_STORE_DIR.mkdir(exist_ok=True)
TASKS: Dict[str, Dict] = {}


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
    safe_path = quote(unquote(parsed.path or "/"), safe="/@%._-~")
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


def sizeof_fmt(num: int) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < step:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= step
    return f"{num:.1f} PB"


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


def parse_list_permissions(line: str) -> str:
    token = (line.split(maxsplit=1)[0] if line else "")
    return token[:10] if len(token) >= 10 else "----------"


def parse_modify_epoch(modify_value: str) -> int:
    if not modify_value:
        return 0
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M%S.%f"):
        try:
            return int(datetime.strptime(modify_value, fmt).timestamp())
        except Exception:
            pass
    return 0


def format_modify_display(modify_value: str) -> str:
    if not modify_value:
        return ""
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M%S.%f"):
        try:
            return datetime.strptime(modify_value, fmt).strftime("%Y/%m/%d %H:%M")
        except Exception:
            pass
    return modify_value


def get_host_usage(ftp: FTP) -> Dict[str, str]:
    try:
        raw = ftp.sendcmd("SITE QUOTA")
        import re
        nums = [int(x) for x in re.findall(r"\d+", raw)]
        if len(nums) >= 2:
            used, total = nums[0], nums[1]
            return {
                "raw": raw,
                "used_bytes": used,
                "total_bytes": total,
                "used_human": sizeof_fmt(used),
                "total_human": sizeof_fmt(total),
            }
        return {"raw": raw}
    except Exception:
        return {"raw": "Unavailable on this FTP server"}


def list_remote(ftp: FTP, path: str):
    path = normalize_remote_path(path)
    entries = []
    try:
        ftp.cwd(path)
    except error_perm:
        raise FileNotFoundError(f"Directory not found: {path}")
    try:
        try:
            for name, facts in ftp.mlsd(facts=["type", "size", "perm", "modify"]):
                if name in (".", ".."):
                    continue
                item_type = "dir" if facts.get("type") == "dir" else "file"
                size = int(facts.get("size", "0") or 0)
                item_path = posixpath.join(path, name) if path != "/" else f"/{name}"
                entries.append({
                    "name": name,
                    "type": item_type,
                    "size": size,
                    "size_human": sizeof_fmt(size) if item_type == "file" else "—",
                    "path": item_path,
                    "perm": facts.get("perm", ""),
                    "modify": format_modify_display(facts.get("modify", "")),
                    "modify_ts": parse_modify_epoch(facts.get("modify", "")),
                })
        except Exception:
            lines = []
            ftp.retrlines("LIST", lines.append)
            for line in lines:
                chunks = line.split(maxsplit=8)
                if len(chunks) < 9:
                    continue
                is_dir = chunks[0].startswith("d")
                raw_size = str(chunks[4])
                size = int(raw_size) if raw_size.isdigit() else 0
                name = chunks[8]
                item_path = posixpath.join(path, name) if path != "/" else f"/{name}"
                entries.append({
                    "name": name,
                    "type": "dir" if is_dir else "file",
                    "size": size,
                    "size_human": sizeof_fmt(size) if not is_dir else "—",
                    "path": item_path,
                    "perm": parse_list_permissions(line),
                    "modify": "",
                    "modify_ts": 0,
                })
    finally:
        ftp.cwd("/")
    return entries


def safe_local_name(name: str) -> str:
    cleaned = os.path.basename((name or "").strip())
    if not cleaned:
        raise ValueError("Invalid file name")
    return cleaned


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
    page = max(1, int(request.args.get("page", "1") or 1))
    page_size = int(request.args.get("page_size", "100") or 100)
    if page_size not in (50, 100, 200, 500, 1000):
        page_size = 100
    sort_by = request.args.get("sort_by", "name")
    sort_dir = request.args.get("sort_dir", "asc")

    ftp = ftp_connect(server_id)
    try:
        items = list_remote(ftp, path)
        if sort_by not in ("name", "size", "type", "perm", "modify"):
            sort_by = "name"
        reverse = sort_dir == "desc"
        if sort_by == "name":
            items.sort(key=lambda x: x["name"].lower(), reverse=reverse)
        elif sort_by == "size":
            items.sort(key=lambda x: x.get("size", 0), reverse=reverse)
        elif sort_by == "modify":
            items.sort(key=lambda x: x.get("modify_ts", 0), reverse=reverse)
        else:
            items.sort(key=lambda x: str(x.get(sort_by, "")).lower(), reverse=reverse)

        total_items = len(items)
        total_size = sum(i.get("size", 0) for i in items if i.get("type") == "file")
        start = (page - 1) * page_size
        end = start + page_size
        paged = items[start:end]
        return jsonify({
            "ok": True,
            "path": normalize_remote_path(path),
            "items": paged,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": (total_items + page_size - 1) // page_size,
            "folder_total_size": total_size,
            "folder_total_size_human": sizeof_fmt(total_size),
            "host_usage": get_host_usage(ftp),
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        })
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


@app.route("/api/chmod", methods=["POST"])
def api_chmod():
    data = request.get_json(force=True)
    server_id = data.get("server_id", "")
    target_path = normalize_remote_path(data.get("target_path", "/"))
    perm = str(data.get("perm", "")).strip()
    if not perm.isdigit() or len(perm) not in (3, 4):
        return jsonify({"ok": False, "error": "Invalid permission. Use 3 or 4 digit number like 644 or 0755"}), 400
    ftp = ftp_connect(server_id)
    try:
        resp = ftp.sendcmd(f"SITE CHMOD {perm} {target_path}")
        return jsonify({"ok": True, "response": resp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        ftp.quit()


@app.route("/api/upload", methods=["POST"])
def api_upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "No files uploaded"}), 400

    saved = []
    for file in files:
        filename = safe_local_name(file.filename)
        if not filename:
            continue
        try:
            file.stream.seek(0)
            dest = LOCAL_STORE_DIR / filename
            with open(dest, "wb") as out:
                out.write(file.stream.read())
            saved.append({"file": filename, "local_path": f"/local/{filename}"})
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to save file {filename}: {e}"}), 400
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/upload-by-url", methods=["POST"])
def api_upload_by_url():
    data = request.get_json(force=True)
    server_id = data.get("server_id", "")
    target_dir = normalize_remote_path(data.get("target_dir", "/"))
    file_url = data.get("file_url", "")
    if not file_url:
        return jsonify({"ok": False, "error": "file_url is required"}), 400
    task_id = f"task-{int(time.time()*1000)}"
    TASKS[task_id] = {"ok": True, "done": False, "progress": 0, "title": "دانلود با لینک", "text": "درحال آماده‌سازی...", "cancel_requested": False}
    def worker():
        try:
            normalized_url = normalize_download_url(file_url)
            filename = posixpath.basename(urlparse(normalized_url).path) or f"download-{int(datetime.utcnow().timestamp())}"
            req = urllib.request.Request(normalized_url, headers={"User-Agent": "Mozilla/5.0 (compatible; RiriFTP/1.0)"})
            with urllib.request.urlopen(req, timeout=180) as response:
                total = int(response.headers.get("Content-Length", "0") or 0)
                done = 0
                start = time.time()
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        if TASKS[task_id].get("cancel_requested"):
                            raise TaskCancelled("عملیات توسط کاربر لغو شد")
                        temp_file.write(chunk)
                        done += len(chunk)
                        speed = done / max(1, (time.time() - start))
                        pct = int((done / total) * 100) if total > 0 else min(99, TASKS[task_id]["progress"] + 1)
                        TASKS[task_id].update({"progress": pct, "text": f"{sizeof_fmt(done)} از {sizeof_fmt(total) if total else 'نامشخص'} | سرعت {sizeof_fmt(int(speed))}/s"})
                    temp_path = temp_file.name
            final_name = safe_local_name(filename)
            os.replace(temp_path, LOCAL_STORE_DIR / final_name)
            TASKS[task_id].update({"done": True, "progress": 100, "saved": [{"file": final_name, "local_path": f"/local/{final_name}"}], "text": "دانلود کامل شد"})
        except TaskCancelled as e:
            TASKS[task_id].update({"ok": False, "done": True, "error": str(e), "cancelled": True, "text": "لغو شد"})
        except Exception as e:
            TASKS[task_id].update({"ok": False, "done": True, "error": f"Failed to download file: {e}"})
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/tasks/<task_id>")
def api_task(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "Task not found"}), 404
    return jsonify(task)


@app.route("/api/local/list")
def api_local_list():
    items = []
    for p in sorted(LOCAL_STORE_DIR.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file():
            size = p.stat().st_size
            items.append({"name": p.name, "size": size, "size_human": sizeof_fmt(size), "path": f"/local/{p.name}"})
    return jsonify({"ok": True, "items": items})


@app.route("/api/local/delete", methods=["POST"])
def api_local_delete():
    data = request.get_json(force=True)
    name = safe_local_name(data.get("name", ""))
    p = LOCAL_STORE_DIR / name
    if not p.exists():
        return jsonify({"ok": False, "error": "File not found"}), 404
    p.unlink()
    return jsonify({"ok": True})


@app.route("/api/local/rename", methods=["POST"])
def api_local_rename():
    data = request.get_json(force=True)
    old_name = safe_local_name(data.get("old_name", ""))
    new_name = safe_local_name(data.get("new_name", ""))
    src = LOCAL_STORE_DIR / old_name
    dst = LOCAL_STORE_DIR / new_name
    if not src.exists():
        return jsonify({"ok": False, "error": "File not found"}), 404
    src.rename(dst)
    return jsonify({"ok": True})


@app.route("/api/local/upload-to-host", methods=["POST"])
def api_local_upload_to_host():
    data = request.get_json(force=True)
    name = safe_local_name(data.get("name", ""))
    server_id = data.get("server_id", "")
    target_dir = normalize_remote_path(data.get("target_dir", "/"))
    base_https = data.get("base_https", "").strip()
    local_file = LOCAL_STORE_DIR / name
    if not local_file.exists():
        return jsonify({"ok": False, "error": "File not found"}), 404
    task_id = f"task-{int(time.time()*1000)}"
    TASKS[task_id] = {"ok": True, "done": False, "progress": 0, "title": "آپلود به هاست", "text": "درحال شروع...", "cancel_requested": False}
    def worker():
        ftp = ftp_connect(server_id)
        try:
            ensure_remote_dir(ftp, target_dir)
            total = local_file.stat().st_size
            sent = 0
            start = time.time()
            with open(local_file, "rb") as src:
                def cb(chunk):
                    nonlocal sent
                    if TASKS[task_id].get("cancel_requested"):
                        raise TaskCancelled("عملیات توسط کاربر لغو شد")
                    sent += len(chunk)
                    speed = sent / max(1, (time.time() - start))
                    TASKS[task_id].update({"progress": int((sent/total)*100), "text": f"{sizeof_fmt(sent)} از {sizeof_fmt(total)} | سرعت {sizeof_fmt(int(speed))}/s"})
                ftp.storbinary(f"STOR {name}", src, blocksize=262144, callback=cb)
            local_file.unlink(missing_ok=True)
            remote_path = posixpath.join(target_dir, name) if target_dir != "/" else f"/{name}"
            TASKS[task_id].update({"done": True, "progress": 100, "remote_path": remote_path, "download_link": (base_https.rstrip('/') + remote_path) if base_https else ""})
        except TaskCancelled as e:
            TASKS[task_id].update({"ok": False, "done": True, "error": str(e), "cancelled": True, "text": "لغو شد"})
        except Exception as e:
            TASKS[task_id].update({"ok": False, "done": True, "error": str(e)})
        finally:
            ftp.quit()
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
def api_task_cancel(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "Task not found"}), 404
    if task.get("done"):
        return jsonify({"ok": False, "error": "Task already completed"}), 400
    task["cancel_requested"] = True
    task["text"] = "درحال لغو..."
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "8085")))
