"""launchd 예약 — 현재 사용자 LaunchAgent 하나.

- 주기 1시간(StartInterval 3600). 시작 지연 0~5분은 run-once --scheduled 가 안에서 준다(launchd 엔 지터가 없다).
- plist·명령줄·환경변수에 토큰을 넣지 않는다. 실행 경로·홈 경로는 절대 경로로 고정.
- 등록·해제는 멱등. 예약 해제는 원문·매핑 삭제와 별개.
- launchctl 호출은 주입 가능한 실행기로만(시험은 모의 실행기, 실제 등록 안 함).
- Windows 예약 도우미는 Phase 2.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys

from . import osutil

LABEL = "local.mycrm.agent"
INTERVAL_SEC = 3600
LAUNCHCTL = "/bin/launchctl"


class ScheduleError(RuntimeError):
    pass


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def package_parent() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plist_path(launch_agents_dir: str | None = None) -> str:
    d = launch_agents_dir or os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
    return os.path.join(d, LABEL + ".plist")


def build_plist(*, python: str, home: str, workdir: str) -> bytes:
    for p in (python, home, workdir):
        if not os.path.isabs(p):
            raise ScheduleError("paths must be absolute")
    logs = os.path.join(home, "logs")
    d = {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "agent", "--home", home, "run-once", "--scheduled"],
        "WorkingDirectory": workdir,
        "StartInterval": INTERVAL_SEC,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": os.path.join(logs, "agent.out.log"),
        "StandardErrorPath": os.path.join(logs, "agent.err.log"),
        "EnvironmentVariables": {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    }
    return plistlib.dumps(d, fmt=plistlib.FMT_XML, sort_keys=True)


def install(*, home: str, python: str | None = None, workdir: str | None = None,
            launch_agents_dir: str | None = None, runner=None, load: bool = True) -> str:
    """'installed' | 'unchanged'. 파일이 같으면 다시 쓰지 않는다."""
    if not osutil.IS_MAC:
        raise ScheduleError("scheduling helper is macOS only in Phase 1")
    runner = runner or _run
    python = python or os.path.realpath(sys.executable)
    workdir = workdir or package_parent()
    data = build_plist(python=python, home=os.path.abspath(home), workdir=workdir)
    path = plist_path(launch_agents_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    osutil.ensure_private_dir(os.path.join(os.path.abspath(home), "logs"))
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return "unchanged"
    osutil.atomic_write(path, data)
    os.chmod(path, 0o644)   # launchd 는 소유자 쓰기만 허용된 plist 를 읽는다. 토큰은 없다
    if load:
        dom = f"gui/{os.getuid()}"
        runner([LAUNCHCTL, "bootout", dom + "/" + LABEL])           # 이전 판 내림(없으면 실패 무시)
        r = runner([LAUNCHCTL, "bootstrap", dom, path])
        if getattr(r, "returncode", 0) != 0:
            raise ScheduleError(f"launchctl bootstrap failed (exit {r.returncode})")
    return "installed"


def remove(*, launch_agents_dir: str | None = None, runner=None, unload: bool = True) -> str:
    """'removed' | 'absent'."""
    if not osutil.IS_MAC:
        raise ScheduleError("scheduling helper is macOS only in Phase 1")
    runner = runner or _run
    path = plist_path(launch_agents_dir)
    if unload:
        runner([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{LABEL}"])
    if os.path.exists(path):
        os.unlink(path)
        return "removed"
    return "absent"


def installed(launch_agents_dir: str | None = None) -> bool:
    return os.path.exists(plist_path(launch_agents_dir))
