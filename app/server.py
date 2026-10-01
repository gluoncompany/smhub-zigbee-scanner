#!/usr/bin/env python3
# Zigbee Scanner for SMHUB - web UI + API. Python stdlib only.
import json, os, re, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanner

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("ZS_DATA", "/opt/zigbee-scanner/data")
PORT = int(os.environ.get("ZS_PORT", "8099"))
Z2M_DATA = os.environ.get("ZS_Z2M_DATA", "/opt/zigbee2mqtt/data")
Z2M_SERVICE = os.environ.get("ZS_Z2M_SERVICE", "zigbee2mqtt")
RC = os.environ.get("ZS_RC", "rc-service")

# message/error are {"key": str, "args": [..]} and are translated by the web UI
state = {"running": False, "message": None, "progress": 0, "error": None, "z2m_restarted": None}
lock = threading.Lock()


def z2m_serial():
    port, baud = "/dev/ttyS1", 115200
    try:
        txt = open(os.path.join(Z2M_DATA, "configuration.yaml"), encoding="utf-8").read()
        m = re.search(r"^serial:\s*\n((?:[ \t]+.*\n?)+)", txt, re.M)
        if m:
            blk = m.group(1)
            p = re.search(r"^\s+port:\s*['\"]?([^'\"\s#]+)", blk, re.M)
            b = re.search(r"^\s+baudrate:\s*(\d+)", blk, re.M)
            if p:
                port = p.group(1)
            if b:
                baud = int(b.group(1))
    except OSError:
        pass
    return port, baud


def z2m_network():
    try:
        d = json.load(open(os.path.join(Z2M_DATA, "coordinator_backup.json"), encoding="utf-8"))
        return {"channel": d.get("channel"), "pan_id": "0x" + str(d.get("pan_id", "")),
                "ext_pan_id": "0x" + str(d.get("extended_pan_id", ""))}
    except (OSError, ValueError):
        return None


def rc(action):
    try:
        r = subprocess.run([RC, Z2M_SERVICE, action], capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def z2m_running():
    code, out = rc("status")
    return code == 0 and "started" in out


def progress(key, pct, args=()):
    state["message"] = {"key": key, "args": list(args)}
    state["progress"] = pct


def err(key, *args):
    return {"key": key, "args": [str(a) for a in args]}


def scan_job(passes):
    was_running = False
    try:
        port, baud = z2m_serial()
        was_running = z2m_running()
        if was_running:
            progress("p_stopping", 2)
            code, out = rc("stop")
            if code != 0:
                state["error"] = err("err_stop", out)
                was_running = False
                return
            time.sleep(3)
        result = scanner.run_scan(port, baud, passes, progress)
        result["serial"] = {"port": port, "baud": baud}
        result["own_network"] = z2m_network()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "last.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
    except scanner.ScanError as e:
        state["error"] = err(e.key, *e.args_)
    except Exception as e:
        state["error"] = err("err_generic", e)
    finally:
        if was_running:
            progress("p_starting", 99)
            code, out = rc("start")
            state["z2m_restarted"] = code == 0
            if code != 0 and not state["error"]:
                state["error"] = err("err_start", out)
        else:
            state["z2m_restarted"] = None
        state["running"] = False
        state["progress"] = 100


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self.send(200, dict(state, z2m_running=z2m_running(), serial=z2m_serial()))
        elif path == "/api/result":
            try:
                with open(os.path.join(DATA_DIR, "last.json"), "rb") as f:
                    self.send(200, f.read())
            except OSError:
                self.send(404, {"error": "no results"})
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/scan":
            return self.send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            passes = max(1, min(20, int(body.get("passes", 5))))
        except (ValueError, TypeError):
            passes = 5
        with lock:
            if state["running"]:
                return self.send(409, {"error": err("err_busy")})
            state.update(running=True, message={"key": "p_preparing", "args": []}, progress=1, error=None, z2m_restarted=None)
        threading.Thread(target=scan_job, args=(passes,), daemon=True).start()
        self.send(202, {"ok": True})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
