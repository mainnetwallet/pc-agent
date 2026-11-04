#!/usr/bin/env python3
"""One-command launcher: backend (FastAPI) + frontend (Vite) + agent.

Usage:
    python run.py                # start everything
    python run.py --no-agent     # backend + frontend only
    python run.py --no-web       # skip the React dashboard
    python run.py --no-server    # skip the relay server
    python run.py --no-browser   # do not open the browser

Press Ctrl+C once to stop everything.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_URL = "http://localhost:5173"


def stream(name, proc):
    """Forward a child's output to our console with a [name] prefix."""
    try:
        for line in iter(proc.stdout.readline, ""):
            print(f"[{name}] {line.rstrip()}", flush=True)
    except ValueError:
        pass


def wait_for_port(port, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        for host in ("127.0.0.1", "::1"):
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                pass
        time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--no-server", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    jobs = []  # (name, cmd, cwd)

    if not args.no_server:
        jobs.append(("server", [sys.executable, "-m", "uvicorn", "main:app",
                                "--host", "0.0.0.0", "--port", "8000"],
                     os.path.join(ROOT, "server")))

    if not args.no_web:
        npm = shutil.which("npm")
        if not npm:
            print("[run] npm not found - install Node.js 18+ or use --no-web")
        else:
            jobs.append(("web", [npm, "run", "dev"], os.path.join(ROOT, "web")))

    if not args.no_agent:
        if sys.platform != "win32":
            print("[run] Agent controls the Windows desktop - skipping on this OS")
        else:
            jobs.append(("agent", [sys.executable, os.path.join("agent", "main.py")], ROOT))

    if not jobs:
        print("[run] nothing to start")
        return 1

    procs = []
    for name, cmd, cwd in jobs:
        print(f"[run] starting {name} ...", flush=True)
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", bufsize=1)
        procs.append((name, p))
        threading.Thread(target=stream, args=(name, p), daemon=True).start()

    if not args.no_web and any(n == "web" for n, _ in procs) and not args.no_browser:
        def open_browser():
            if wait_for_port(5173):
                webbrowser.open(WEB_URL)
        threading.Thread(target=open_browser, daemon=True).start()

    print("[run] all started. Dashboard: " + WEB_URL + "  |  API: http://localhost:8000")
    print("[run] Press Ctrl+C to stop everything.", flush=True)

    reported = set()
    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                code = p.poll()
                if code is not None and name not in reported:
                    reported.add(name)
                    print(f"[run] {name} exited with code {code}", flush=True)
            if len(reported) == len(procs):
                break
    except KeyboardInterrupt:
        print("\n[run] stopping ...", flush=True)
    finally:
        for name, p in procs:
            if p.poll() is None:
                if sys.platform == "win32":
                    # kill whole tree (npm spawns child node processes)
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
