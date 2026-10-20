"""OS 분기 — 권한·원자적 쓰기·파일 잠금·KST.

Windows 에선 chmod 가 의미 없으므로 실패하지 않고 넘어간다(권한은 사용자 프로필 폴더에 맡긴다).
fcntl 은 POSIX 에서만 import 한다 — Windows 에서 모듈 import 가 깨지지 않게.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
KST = _dt.timezone(_dt.timedelta(hours=9), "KST")


def kst_now() -> _dt.datetime:
    return _dt.datetime.now(KST)


def kst_today() -> str:
    return kst_now().date().isoformat()


def ensure_private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not IS_WINDOWS:
        os.chmod(path, 0o700)
    return path


def set_private_file(path: str) -> None:
    if not IS_WINDOWS:
        os.chmod(path, 0o600)


def atomic_write(path: str, data: bytes) -> None:
    """같은 폴더 임시 파일(0600) → fsync → os.replace. 중간에 죽어도 반쪽 파일이 남지 않는다."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        if not IS_WINDOWS:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if not IS_WINDOWS:
        try:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


class LockBusy(RuntimeError):
    pass


class FileLock:
    """비차단 배타 잠금. 이미 잡혀 있으면 LockBusy."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> "FileLock":
        fh = open(self.path, "a+b")
        try:
            if IS_WINDOWS:
                import msvcrt
                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise LockBusy(os.path.basename(self.path)) from None
            else:
                import fcntl
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise LockBusy(os.path.basename(self.path)) from None
        except BaseException:
            fh.close()
            raise
        set_private_file(self.path)
        self._fh = fh
        return self

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt
                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
