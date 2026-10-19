"""점검. 결과마다 상태(ok/warn/fail/skip)·오류 코드·설명·분류·다음 조치를 단다.

분류: user  = 사용자가 직접 조치
      env   = 지원하지 않는 환경
      restore = 재설치·롤백 필요
      defect  = 제품 결함 후보 (issue 로 남길 것)
모델은 부르지 않는다. 로그인 여부는 모델 호출 없이 확인할 방법이 없어 '미실행'으로 둔다.
"""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import SCHEMA_VERSION, VERSION, data as D, model, paths, schema, store

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
LABEL = {OK: "정상", WARN: "경고", FAIL: "실패", SKIP: "미실행"}


def _r(code, status, msg, action="", kind=""):
    return {"code": code, "status": status, "msg": msg, "action": action, "kind": kind}


def env_checks(port):
    out = []
    v = sys.version_info
    out.append(_r("ENV-PY", OK if v >= (3, 9) else FAIL, f"Python {v.major}.{v.minor}.{v.micro}",
                  "" if v >= (3, 9) else "Python 3.9 이상을 설치하세요 (설치 가이드 1절).", "user"))
    osname = platform.system()
    wsl = osname == "Linux" and "microsoft" in platform.release().lower()
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC") or "?"
    label = {"Darwin": "macOS", "Windows": "Windows 네이티브"}.get(osname, osname)
    if wsl:
        label = "Windows WSL"
    st = OK if osname in ("Darwin", "Windows") else WARN
    note = "" if st == OK else ("WSL 은 네이티브 Windows 와 별도 환경입니다. 경로·CLI 를 섞지 마세요. 지원표상 미검증."
                                if wsl else "지원 대상(macOS·Windows) 밖의 OS 입니다. 핵심 흐름만 시도하세요.")
    out.append(_r("ENV-OS", st, f"{label} {platform.release()} · {platform.machine()} · 셸 {Path(shell).name}",
                  note, "env"))
    exe = model.find_claude()
    if not exe:
        out.append(_r("ENV-CLI", FAIL, "Claude Code CLI(claude) 없음",
                      "설치 가이드의 Claude Code 절을 따라 설치·로그인하세요. 대시보드 조회는 CLI 없이도 됩니다.", "user"))
    else:
        try:
            r = subprocess.run(([sys.executable] if exe.endswith(".py") else []) + [exe, "--version"],
                               capture_output=True, text=True, timeout=20)
            ver = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "?"
            out.append(_r("ENV-CLI", OK, f"claude {ver}"))
        except (OSError, subprocess.TimeoutExpired) as e:
            out.append(_r("ENV-CLI", WARN, f"claude 를 찾았지만 실행이 안 됩니다: {e}",
                          "터미널에서 claude --version 을 직접 실행해 보세요.", "user"))
        out.append(_r("ENV-LOGIN", SKIP, "로그인 상태는 첫 정리(digest) 때 확인합니다",
                      "로그인 만료가 뜨면 claude 를 열고 /login.", "user"))
    out.append(_r("ENV-GIT", OK if shutil.which("git") else WARN,
                  "git 있음" if shutil.which("git") else "git 없음 — 필수 아님",
                  "" if shutil.which("git") else "버전 기록을 원하면 git 을 설치하세요. 없어도 백업은 동작합니다."))
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        out.append(_r("ENV-PORT", OK, f"포트 {port} 비어 있음"))
    except OSError:
        out.append(_r("ENV-PORT", WARN, f"포트 {port} 사용 중",
                      f"이미 대시보드가 떠 있을 수 있습니다. 아니면 serve --port {port + 1} 로 바꾸세요.", "user"))
    finally:
        s.close()
    return out


def data_checks(data):
    out = []
    if data is None:
        return [_r("DATA-UNSET", FAIL, "데이터 폴더가 정해지지 않음",
                   "python3 crm.py init --data <폴더> 로 만드세요.", "user")]
    data = Path(data)
    if not (data / "meta.json").exists():
        return [_r("DATA-MISSING", FAIL, f"데이터 폴더 아님: {paths.mask_path(data)}",
                   "경로가 옮겨졌다면 init --data <새 경로> 로 위치만 다시 지정하세요(기존 파일은 안 지웁니다).", "user")]
    p = str(data)
    flags = [x for x, hit in (("한글", re.search(r"[^\x00-\x7f]", p)), ("공백", " " in p)) if hit]
    out.append(_r("DATA-PATH", OK, f"데이터 폴더 {paths.mask_path(data)}" + (f" ({'·'.join(flags)} 포함 경로)" if flags else "")))
    probe = paths.sys_dir(data) / ".write-probe"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        out.append(_r("DATA-WRITE", OK, "쓰기 권한 있음"))
    except OSError as e:
        out.append(_r("DATA-WRITE", FAIL, f"쓰기 불가: {e.strerror}",
                      "폴더 권한을 확인하거나 다른 위치를 데이터 폴더로 지정하세요.", "user"))
    try:
        kind = json.loads((data / "meta.json").read_text(encoding="utf-8")).get("kind")
        if kind == "sample":
            out.append(_r("DATA-SAMPLE", WARN, "지금 열린 것은 가짜 샘플 데이터입니다",
                          "내 자료는 init --data <다른 폴더> 로 따로 만드세요.", "user"))
    except (OSError, ValueError):
        pass
    loaded = {}
    for name in paths.SETTINGS:
        f = data / name
        if not f.exists():
            out.append(_r("DATA-FILE", FAIL, f"{name} 없음",
                          "init --data 를 다시 실행하면 빠진 파일만 템플릿으로 채웁니다.", "restore"))
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append(_r("DATA-CORRUPT", FAIL, f"{name} 손상 (JSON 형식 오류: 줄 {getattr(e, 'lineno', '?')})",
                          "python3 crm.py backups 로 마지막 정상본을 고르고 restore <백업ID>.", "restore"))
            continue
        errs = schema.VALIDATORS[name](d)
        if errs:
            code = "DATA-BLOCK" if any("알 수 없는 블록" in e for e in errs) else "DATA-SCHEMA"
            kind = "restore" if code == "DATA-SCHEMA" and any("새 버전" in e for e in errs) else "user"
            out.append(_r(code, FAIL, f"{name}: " + "; ".join(errs),
                          "restore 로 마지막 정상본을 되돌리거나, 업데이트 가이드를 확인하세요.", kind))
            continue
        loaded[name] = d
    if not [r for r in out if r["status"] == FAIL and r["code"].startswith("DATA-") and r["code"] != "DATA-WRITE"]:
        out.append(_r("DATA-SCHEMA", OK, f"설정 파일 {len(loaded)}개 검사 통과 (schemaVersion {SCHEMA_VERSION})"))
    # 링크: 후보가 가리키는 기록, 등록된 자료 위치
    if "candidates.json" in loaded:
        miss = sorted({c["evidence"]["record"] for c in loaded["candidates.json"]["candidates"]
                       if not (data / "journal" / f"{c['evidence']['record']}.md").exists()})
        out.append(_r("DATA-LINK", WARN if miss else OK,
                      f"원문 없는 후보 근거: {', '.join(miss)}" if miss else "후보의 원문 링크 정상",
                      "기록 파일이 옮겨졌는지 journal 폴더를 확인하세요. 후보는 지우지 않았습니다." if miss else "", "user"))
    if "sources.json" in loaded:
        gone = [s["id"] for s in loaded["sources.json"]["sources"] if not Path(s["path"]).exists()]
        if gone:
            out.append(_r("DATA-SOURCE", WARN, f"자료 위치를 찾을 수 없음: {', '.join(gone)}",
                          "자료 폴더가 옮겨졌다면 source add 로 새 위치를 등록하세요.", "user"))
    lf = paths.sys_dir(data) / "lock"
    if lf.exists():
        age = time.time() - lf.stat().st_mtime
        out.append(_r("RUN-LOCK", WARN if age > store.LOCK_STALE_SEC else OK,
                      f"잠금 있음 ({int(age)}초 전) — " + ("중단된 실행의 흔적" if age > store.LOCK_STALE_SEC else "실행 중"),
                      "다음 쓰기 명령이 자동으로 정리합니다." if age > store.LOCK_STALE_SEC else ""))
    runs = store.read_runs(data)
    dg = runs.get("digest")
    if dg and dg.get("state") == "failed":
        out.append(_r("RUN-LAST", WARN, f"마지막 정리 실패: {dg.get('error_code')} {dg.get('error', '')}",
                      dg.get("action") or "한 번만 다시 실행하세요.", "user"))
    elif dg and dg.get("state") == "running":
        out.append(_r("RUN-LAST", WARN, "정리 실행이 '실행 중'으로 남아 있습니다 — 중단됐을 수 있습니다",
                      "같은 digest 를 다시 실행하면 이어서 처리합니다(중복 후보는 만들지 않음).", "user"))
    bks = store.list_backups(data)
    out.append(_r("BACKUP", OK if bks else WARN,
                  f"백업 {len(bks)}개, 최근 {bks[0]['id']}" if bks else "백업 없음 — 복구할 정상본이 없습니다",
                  "" if bks else "python3 crm.py backup 으로 지금 상태를 먼저 남기세요.", "user"))
    return out


def run(data, port=8765):
    res = env_checks(port) + data_checks(data)
    first = next((r for r in res if r["status"] == FAIL), None) or next(
        (r for r in res if r["status"] == WARN and r["action"]), None)
    return {"version": VERSION, "checked": store.now(), "results": res,
            "next": (first["action"] if first else "핵심 점검 통과. 다음 단계로 진행하세요."),
            "ok": not any(r["status"] == FAIL for r in res)}


SECRET_RE = re.compile(r"(sk-ant-[\w-]+|xox[abpr]-[\w-]+|ghp_\w+|[\w.+-]+@[\w-]+\.[\w.]+|(?i:password|token)\s*[:=]\s*\S+)")


def diag(data, port=8765):
    """지원용 진단. 원문·질문 문장·후보 문장은 넣지 않는다. 경로·비밀값은 가린다."""
    rep = run(data, port)
    counts = {}
    if data and (Path(data) / "meta.json").exists():
        counts["journal"] = len(list(Path(data).glob("journal/j-*.md")))
        for name, key in (("questions.json", "questions"), ("candidates.json", "candidates"), ("tabs.json", "tabs")):
            try:
                counts[key] = len(json.loads((Path(data) / name).read_text(encoding="utf-8")).get(key, []))
            except (OSError, ValueError):
                counts[key] = "읽기 실패"
        counts["backups"] = len(store.list_backups(data))
        runs = store.read_runs(data)
    else:
        runs = {}
    body = {"product_version": VERSION, "schema_version": SCHEMA_VERSION, "created": store.now(),
            "platform": {"os": platform.system(), "release": platform.release(), "machine": platform.machine(),
                         "python": platform.python_version()},
            "checks": rep["results"], "counts": counts,
            "runs": {k: {x: v.get(x) for x in ("state", "started", "finished", "last_success", "error_code")}
                     for k, v in runs.items()},
            "note": "원문·질문·판단 문장·경로는 포함하지 않았습니다. 보내기 전에 열어 확인하세요."}
    text = SECRET_RE.sub("<가림>", paths.mask_path(json.dumps(body, ensure_ascii=False, indent=2), data))
    return text
