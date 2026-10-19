"""안전한 쓰기·잠금·백업·복구.

규칙: 임시 파일에 쓰고 → 다시 읽어 검사하고 → os.replace 로 교체한다. 검사에 실패하면 원래 파일은 그대로다.
잠금은 fcntl 대신 O_CREAT|O_EXCL 잠금 파일을 쓴다 — Windows 에서도 같은 코드로 돈다.
오래된 잠금은 나이로만 판정한다. Windows 에서 os.kill(pid, 0) 은 프로세스를 종료시키므로 쓰지 않는다.
"""
import datetime as _dt
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from . import paths

LOCK_STALE_SEC = 15 * 60


class CrmError(Exception):
    """사용자에게 보여 줄 오류. code 는 가이드의 오류 표와 같은 이름이다."""

    def __init__(self, code, message, action=""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action


def now():
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def stamp():
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def read_json(path):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CrmError("FILE-MISSING", f"파일이 없습니다: {path.name}",
                         "python3 crm.py doctor 로 무엇이 빠졌는지 확인하세요.")
    except ValueError as e:
        raise CrmError("FILE-CORRUPT", f"{path.name} 을(를) 읽을 수 없습니다 (JSON 형식 오류: {e})",
                         "python3 crm.py backups 로 백업을 보고 restore 로 되돌리세요.")


def write_text_atomic(path, text, check=None):
    """text 를 path 에 원자적으로 쓴다. check(읽어 온 문자열) 가 예외를 내면 교체하지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if check:
            check(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json_atomic(path, data, validate=None):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def check(s):
        loaded = json.loads(s)
        if validate:
            errs = validate(loaded)
            if errs:
                raise CrmError("SCHEMA-INVALID", "저장 전 검사 실패: " + "; ".join(errs),
                                 "기존 파일은 그대로입니다. 입력값을 고쳐 다시 실행하세요.")
    write_text_atomic(path, text, check)


@contextmanager
def lock(data, what):
    """데이터 폴더 쓰기 잠금. 같은 데이터에 두 명령이 동시에 쓰지 못하게 한다."""
    d = paths.sys_dir(data)
    d.mkdir(parents=True, exist_ok=True)
    lf = d / "lock"
    if lf.exists() and time.time() - lf.stat().st_mtime > LOCK_STALE_SEC:
        lf.unlink()          # 15분 넘은 잠금은 중단된 실행의 흔적으로 본다
    try:
        fd = os.open(str(lf), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        info = lf.read_text(encoding="utf-8", errors="replace").strip()
        raise CrmError("RUN-LOCKED", f"다른 작업이 실행 중입니다 ({info}).",
                         "끝날 때까지 기다리세요. 15분이 지나면 자동으로 풀립니다.")
    try:
        os.write(fd, f"{what} pid={os.getpid()} at={now()}".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            lf.unlink()
        except FileNotFoundError:
            pass


# ── 백업·복구 ─────────────────────────────────────────────

def _managed_files(data):
    data = Path(data)
    out = [data / n for n in paths.SETTINGS if (data / n).exists()]
    for d in paths.USER_DIRS:
        if (data / d).is_dir():
            out += sorted(p for p in (data / d).rglob("*") if p.is_file())
    if (data / "issue-log.md").exists():
        out.append(data / "issue-log.md")
    return out


def backup(data, label):
    """설정·기록·정리본을 .crm/backups/<시각>-<라벨>/ 로 복사한다. 백업 ID 를 돌려준다."""
    data = Path(data)
    bid = f"{stamp()}-{label}"
    dest = paths.sys_dir(data) / "backups" / bid
    n = 1
    while dest.exists():                      # 같은 초에 두 번 찍힌 경우
        n += 1
        dest = dest.with_name(f"{bid}-{n}")
    files = _managed_files(data)
    for f in files:
        rel = f.relative_to(data)
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest / rel)
    (dest / "backup.json").write_text(json.dumps({
        "id": dest.name, "label": label, "created": now(),
        "files": [str(f.relative_to(data).as_posix()) for f in files],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest.name


def list_backups(data):
    root = paths.sys_dir(data) / "backups"
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir(), reverse=True):
            try:
                out.append(json.loads((d / "backup.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                out.append({"id": d.name, "label": "?", "created": "", "files": [], "broken": True})
    return out


def restore(data, bid):
    """백업을 되돌린다. 지금 상태를 먼저 백업하고, 백업에 있는 파일만 덮어쓴다.
    백업 뒤에 새로 생긴 기록은 지우지 않는다 — 원문 삭제 금지."""
    data = Path(data)
    src = paths.sys_dir(data) / "backups" / bid
    meta_f = src / "backup.json"
    if not meta_f.exists():
        raise CrmError("BACKUP-MISSING", f"백업 {bid} 이(가) 없습니다.",
                         "python3 crm.py backups 로 ID 를 확인하세요.")
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    pre = backup(data, "before-restore")
    for rel in meta["files"]:
        s = src / rel
        if not s.exists():
            raise CrmError("BACKUP-BROKEN", f"백업 안 파일이 빠졌습니다: {rel}",
                             f"다른 백업을 고르세요. 복구 전 상태는 {pre} 에 있습니다.")
        t = data / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.with_name(f".{t.name}.restore-tmp")
        shutil.copy2(s, tmp)
        os.replace(tmp, t)
    kept = [str(f.relative_to(data).as_posix()) for f in _managed_files(data)
            if str(f.relative_to(data).as_posix()) not in meta["files"]]
    return {"restored": meta["files"], "kept_newer": kept, "pre_restore_backup": pre}


# ── 실행 상태 (정본 아님) ──────────────────────────────────

def runs_path(data):
    return paths.sys_dir(data) / "runs.json"


def read_runs(data):
    try:
        return json.loads(runs_path(data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_run(data, name, **fields):
    runs = read_runs(data)
    cur = runs.get(name, {})
    cur.update(fields)
    runs[name] = cur
    write_json_atomic(runs_path(data), runs)
