#!/usr/bin/env python3
"""분배 큐(assignments.jsonl)의 유일한 읽기·쓰기 자리 — 잠금 + 원자 교체.

왜 있나 (2026-08-27): 서버(checkpoint_board)·실행 워커(run_worker)·정리
워커(brief_worker)가 각자 파일 전체를 읽어 고쳐 통째로 다시 썼다. 잠금이 없어
워커가 도는 동안 사용자가 카드를 누르면 읽기~쓰기 사이에 서로를 덮을 수 있었다
(잃으면 복구 불가 — 이 파일만 git 밖이다). 이제 모든 변경은 여기의 flock 아래서만.

쓰는 법:
    from checkpoint_store import read, append, patch, mutate
    read()               # 잠금 없이 읽는다 — 교체가 원자적이라 항상 온전한 파일을 본다
    append(rec)          # 레코드 하나 추가
    patch(ts, k=v, ...)  # 그 ts 레코드에 필드 병합 (found 여부 반환)
    mutate(fn)           # 복잡한 변경 — fn(rows)가 rows를 고치고 반환값을 돌려준다.
                         #   fn이 STOP을 반환하면 저장하지 않는다 (검사 후 거절 경로).

규칙: 이 모듈 밖에서 assignments.jsonl 을 열어 쓰지 않는다. 읽기 전용은 read()로.
"""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
ASSIGN = P / "_workspace/checkpoint/assignments.jsonl"
_LOCK = ASSIGN.with_suffix(".jsonl.lock")

STOP = object()   # mutate의 fn이 "저장하지 마라"를 알리는 표식


@contextmanager
def _locked():
    ASSIGN.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)   # 프로세스가 죽으면 OS가 풀어준다
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read(path=None):
    """줄 단위 JSON 읽기. 깨진 줄은 건너뛴다(빈 줄 등). 잠금 불필요 —
    쓰기가 전부 원자 교체(os.replace)라 읽는 쪽은 언제나 온전한 파일을 본다."""
    p = path or ASSIGN
    out = []
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _write(rows):
    """같은 디렉터리에 임시 파일로 다 쓴 뒤 원자 교체 — 중간에 죽어도 반쪽 파일이 없다."""
    fd, tmp = tempfile.mkstemp(dir=str(ASSIGN.parent), prefix=".assignments.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ASSIGN)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mutate(fn):
    """잠금 아래서 rows를 읽어 fn(rows)를 실행하고 저장한다.
    fn 반환값이 STOP이면 저장하지 않는다. 그 외 반환값은 그대로 돌려준다."""
    with _locked():
        rows = read()
        ret = fn(rows)
        if ret is not STOP:
            _write(rows)
        return ret


def append(rec):
    return mutate(lambda rows: rows.append(rec))


def patch(ts, **fields):
    """그 ts 레코드에 필드 병합. 잠금 아래서 다시 읽어 병합하므로
    도는 사이 다른 쪽이 쓴 변경을 덮지 않는다."""
    def _do(rows):
        found = False
        for r in rows:
            if r.get("ts") == ts:
                r.update(fields)
                found = True
        return found if found else STOP
    return mutate(_do) is True
