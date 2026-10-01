"""Prove the API fits the free-tier VM: run it as in production and measure its peak memory.

Starts the API (one uvicorn worker, a throwaway database, mock hazard data, no LLM keys),
signs in, runs a batch of real submissions and reviews through HTTP, then reads the server
process's peak resident memory. Fails if the peak exceeds --max-mb. The systemd unit throttles
the API at 320 MB and stops it at 384 MB, so the default limit leaves room for LLM traffic.

    uv run python deploy/memcheck.py              # 60 submissions, fail above 300 MB
    uv run python deploy/memcheck.py --submissions 200 --max-mb 250

Works on Linux (CI, the server) and Windows.
"""

import argparse
import ctypes
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ["clean", "high_risk", "decline", "incomplete"]


def _windows_descendants(pid: int) -> list[int]:
    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    parents: dict[int, int] = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
    while ok:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    found, frontier = [], [pid]
    while frontier:
        children = [c for c, p in parents.items() if p in frontier and c not in found]
        found += children
        frontier = children
    return found


def peak_and_current_mb(pid: int) -> tuple[float, float]:
    if sys.platform.startswith("linux"):
        fields = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            fields[key] = value.strip()
        return int(fields["VmHWM"].split()[0]) / 1024, int(fields["VmRSS"].split()[0]) / 1024
    if sys.platform == "win32":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        def measure(one_pid: int) -> tuple[float, float]:
            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, one_pid)
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            ctypes.windll.kernel32.CloseHandle(handle)
            return counters.PeakWorkingSetSize / 2**20, counters.WorkingSetSize / 2**20

        # A venv's python.exe on Windows is a launcher that runs the real interpreter as a
        # child process, so measure every process in the tree and report the largest.
        return max((measure(p) for p in [pid, *_windows_descendants(pid)]), key=lambda m: m[0])
    raise SystemExit(f"memory measurement is not implemented for {sys.platform}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the API's peak memory under load")
    parser.add_argument("--submissions", type=int, default=60)
    parser.add_argument("--max-mb", type=float, default=300)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="uw-memcheck-"))
    db = workdir / "memcheck.db"
    password = secrets.token_urlsafe(18)  # throwaway admin for this run only
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "HAZARD_PROVIDER": "mock",
        "LLM_RISK_ASSESSMENT": "false",
        # Empty keys override any .env on this machine, so no LLM or Apify calls are made.
        "GROQ_API_KEY": "",
        "GEMINI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "APIFY_TOKEN": "",
    }
    python = sys.executable
    subprocess.run(
        [
            python,
            "-m",
            "underwriting_agent.auth",
            "create",
            "--username",
            "memcheck",
            "--name",
            "Memory Check",
            "--role",
            "admin",
            "--password-stdin",
        ],
        input=password + "\n",
        text=True,
        env=env,
        cwd=workdir,
        check=True,
        capture_output=True,
    )
    server = subprocess.Popen(
        [
            python,
            "-m",
            "uvicorn",
            "underwriting_agent.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--workers",
            "1",
            "--log-level",
            "warning",
        ],
        env=env,
        cwd=workdir,
        stdout=subprocess.DEVNULL,  # the per-request logs are not needed here
    )
    base = f"http://127.0.0.1:{args.port}"
    try:
        with httpx.Client(base_url=base, timeout=30) as client:
            for _ in range(60):
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise SystemExit("the API did not start")
            idle_peak, _ = peak_and_current_mb(server.pid)

            token = client.post("/auth/login", json={"username": "memcheck", "password": password})
            client.headers["Authorization"] = f"Bearer {token.json()['token']}"
            samples = [
                json.loads((ROOT / "src/underwriting_agent/samples" / f"{s}.json").read_text())
                for s in SAMPLES
            ]
            started, reviewed = time.perf_counter(), 0
            for i in range(args.submissions):
                body = client.post("/underwrite", json=samples[i % len(samples)]).json()
                if body.get("status") == "referred":
                    note = {"action": "approve", "note": "Memory check review of a referral."}
                    client.post(f"/submissions/{body['submission_id']}/review", json=note)
                    reviewed += 1
            elapsed = time.perf_counter() - started
            listed = client.get("/submissions", params={"limit": 200}).json()["total"]
            verified = client.get("/audit/verify").json()
        peak, current = peak_and_current_mb(server.pid)
    finally:
        server.terminate()
        server.wait(timeout=15)

    print(f"idle after start:  {idle_peak:6.1f} MB")
    print(f"peak under load:   {peak:6.1f} MB   (limit {args.max_mb:.0f} MB)")
    print(f"at the end:        {current:6.1f} MB")
    print(
        f"load:              {args.submissions} submissions, {reviewed} reviews in {elapsed:.1f} s"
    )
    print(
        f"database:          {listed} submissions, audit chain ok={verified['ok']} "
        f"({verified['entries_checked']} entries)"
    )
    if not verified["ok"]:
        print("FAILED: the audit chain did not verify")
        return 1
    if peak > args.max_mb:
        print(f"FAILED: peak {peak:.1f} MB is above {args.max_mb:.0f} MB")
        return 1
    print("OK: fits the 1 GB free-tier VM budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
