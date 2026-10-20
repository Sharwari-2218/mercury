"""Start everything the backend needs, from ONE terminal.

    cd backend
    python dev.py              # Chroma + RQ worker + API (with --reload)
    python dev.py --no-api     # Chroma + worker only (run uvicorn yourself)

Press Ctrl+C once to stop all of them. Redis must already be running
(it's a separate program, e.g. `redis-server`, Memurai on Windows, or Docker).

Output from each process is prefixed with its name: [chroma], [worker], [api].
"""
import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent
load_dotenv(BACKEND / ".env")

IS_WINDOWS = os.name == "nt"
DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND / "data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = BACKEND / DATA_DIR
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8001"))
API_PORT = int(os.getenv("API_PORT", "8000"))
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

COLORS = {"chroma": "\033[35m", "worker": "\033[33m", "api": "\033[36m"}
RESET = "\033[0m"
if IS_WINDOWS:
    os.system("")  # turns on ANSI colours in the Windows console


def tool(name: str) -> str:
    """Find a console script (chroma, rq) inside the active venv first."""
    scripts = Path(sys.executable).parent
    for candidate in (scripts / name, scripts / f"{name}.exe"):
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        sys.exit(f"Can't find '{name}'. Activate the venv and run: pip install -r requirements.txt")
    return found


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def pipe_output(name: str, proc: subprocess.Popen) -> None:
    color = COLORS.get(name, "")
    for line in iter(proc.stdout.readline, ""):
        print(f"{color}[{name}]{RESET} {line}", end="", flush=True)


def start(name: str, cmd: list[str], env: dict) -> subprocess.Popen:
    kwargs = {}
    if IS_WINDOWS:
        # Own process group, so Ctrl+C reaches only us and we stop children cleanly.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        cmd, cwd=BACKEND, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
    )
    threading.Thread(target=pipe_output, args=(name, proc), daemon=True).start()
    print(f"started {name} (pid {proc.pid})", flush=True)
    return proc


def wait_for_chroma(proc: subprocess.Popen, timeout: int = 60) -> None:
    url = f"http://{CHROMA_HOST}:{CHROMA_PORT}/api/v2/heartbeat"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit("Chroma exited during startup; see the [chroma] lines above.")
        try:
            urllib.request.urlopen(url, timeout=2)
            print(f"Chroma is ready on port {CHROMA_PORT}", flush=True)
            return
        except OSError:
            time.sleep(1)
    sys.exit(f"Chroma didn't answer on {url} within {timeout}s.")


def stop_all(procs: dict[str, subprocess.Popen]) -> None:
    # Stop in reverse order: API and worker first, Chroma last.
    for name, proc in reversed(list(procs.items())):
        if proc.poll() is None:
            print(f"stopping {name}...", flush=True)
            proc.terminate()
    for proc in procs.values():
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-api", action="store_true", help="don't start uvicorn")
    args = parser.parse_args()

    if CHROMA_PORT == API_PORT:
        sys.exit(f"CHROMA_PORT and the API both use {API_PORT}. Set CHROMA_PORT=8001 in .env.")
    if not port_open(REDIS_HOST, REDIS_PORT):
        sys.exit(f"Redis isn't running on {REDIS_HOST}:{REDIS_PORT}. Start it first, then rerun.")
    if port_open(CHROMA_HOST, CHROMA_PORT):
        sys.exit(f"Port {CHROMA_PORT} is already in use (is Chroma already running?).")

    env = {**os.environ, "CHROMA_HOST": CHROMA_HOST, "CHROMA_PORT": str(CHROMA_PORT),
           "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    (DATA_DIR / "chroma_db").mkdir(parents=True, exist_ok=True)

    procs: dict[str, subprocess.Popen] = {}
    try:
        procs["chroma"] = start("chroma", [
            tool("chroma"), "run", "--path", str(DATA_DIR / "chroma_db"),
            "--host", CHROMA_HOST, "--port", str(CHROMA_PORT),
        ], env)
        wait_for_chroma(procs["chroma"])  # worker/API must not start before Chroma

        worker_cmd = [tool("rq"), "worker", "ingest"]
        if IS_WINDOWS:
            worker_cmd += ["--worker-class", "rq.worker.SimpleWorker"]  # no fork() on Windows
        procs["worker"] = start("worker", worker_cmd, env)

        if not args.no_api:
            procs["api"] = start("api", [
                sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--port", str(API_PORT),
            ], env)
            print(f"API: http://localhost:{API_PORT}  (docs at /docs)", flush=True)

        print("All running. Press Ctrl+C to stop everything.", flush=True)
        while True:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    print(f"{name} exited with code {proc.returncode}; stopping the rest.", flush=True)
                    return
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        stop_all(procs)
        print("All stopped.")


if __name__ == "__main__":
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()
