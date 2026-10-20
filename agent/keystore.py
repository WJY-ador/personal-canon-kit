"""자격 증명 보관 — macOS Keychain(`security` CLI). 평문 파일 대체 없음.

비밀값은 명령줄 인자에 넣지 않는다: 쓰기는 `security -i` 의 표준 입력으로 명령을 넘기고,
읽기는 `find-generic-password -w` 의 표준 출력으로 받는다.
Windows(자격 증명 관리자)는 인터페이스만 두고 미구현 오류로 끝낸다.
"""
from __future__ import annotations

import re
import subprocess

from . import osutil

_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_SECRET = re.compile(r"^[A-Za-z0-9_.-]{16,512}$")
SECURITY_BIN = "/usr/bin/security"
_NOT_FOUND = 44   # errSecItemNotFound 의 security CLI 종료 코드


class KeyStoreError(RuntimeError):
    """Keychain 접근 불가·거부. 메시지에 비밀값을 넣지 않는다."""


class KeyStoreUnavailable(KeyStoreError):
    pass


def _run(argv, stdin_text=None, timeout=30):
    return subprocess.run(argv, input=stdin_text, capture_output=True, text=True, timeout=timeout)


class KeyStore:
    def get(self, account: str) -> str | None:
        raise NotImplementedError

    def set(self, account: str, secret: str) -> None:
        raise NotImplementedError

    def delete(self, account: str) -> bool:
        raise NotImplementedError


class MacKeychain(KeyStore):
    def __init__(self, service: str, runner=None):
        if not _SAFE.match(service or ""):
            raise KeyStoreError("service name")
        self.service = service
        self.runner = runner or _run

    def _acct(self, account: str) -> str:
        if not _SAFE.match(account or ""):
            raise KeyStoreError("account name")
        return account

    def get(self, account: str) -> str | None:
        a = self._acct(account)
        try:
            r = self.runner([SECURITY_BIN, "find-generic-password", "-s", self.service, "-a", a, "-w"])
        except (OSError, subprocess.SubprocessError):
            raise KeyStoreError("keychain read failed") from None
        if r.returncode == _NOT_FOUND:
            return None
        if r.returncode != 0:
            raise KeyStoreError(f"keychain read failed (exit {r.returncode})")
        val = (r.stdout or "").rstrip("\n")
        return val or None

    def set(self, account: str, secret: str) -> None:
        a = self._acct(account)
        if not isinstance(secret, str) or not _SAFE_SECRET.match(secret):
            raise KeyStoreError("secret has unsupported characters")
        # -U: 있으면 갱신. 비밀값은 표준 입력 명령 안에만 있다(argv 엔 없음).
        cmd = f'add-generic-password -U -s "{self.service}" -a "{a}" -w "{secret}"\n'
        try:
            r = self.runner([SECURITY_BIN, "-i"], stdin_text=cmd)
        except (OSError, subprocess.SubprocessError):
            raise KeyStoreError("keychain write failed") from None
        if r.returncode != 0:
            raise KeyStoreError(f"keychain write failed (exit {r.returncode})")
        # 쓰기 확인 — 접근 불가면 여기서 실패 종료
        if self.get(a) != secret:
            raise KeyStoreError("keychain write not readable")

    def delete(self, account: str) -> bool:
        a = self._acct(account)
        try:
            r = self.runner([SECURITY_BIN, "delete-generic-password", "-s", self.service, "-a", a])
        except (OSError, subprocess.SubprocessError):
            raise KeyStoreError("keychain delete failed") from None
        if r.returncode == _NOT_FOUND:
            return False
        if r.returncode != 0:
            raise KeyStoreError(f"keychain delete failed (exit {r.returncode})")
        return True


class WindowsCredentialStore(KeyStore):
    """Windows 자격 증명 관리자 어댑터 — Phase 1 미구현(인터페이스만)."""

    def __init__(self, service: str, runner=None):
        self.service = service

    def _no(self):
        raise KeyStoreUnavailable("Windows Credential Manager adapter is not implemented yet")

    def get(self, account):
        self._no()

    def set(self, account, secret):
        self._no()

    def delete(self, account):
        self._no()


class UnsupportedKeyStore(KeyStore):
    def __init__(self, service: str, runner=None):
        self.service = service

    def _no(self):
        raise KeyStoreUnavailable("no supported credential store on this OS")

    def get(self, account):
        self._no()

    def set(self, account, secret):
        self._no()

    def delete(self, account):
        self._no()


def for_platform(service: str, runner=None) -> KeyStore:
    if osutil.IS_MAC:
        return MacKeychain(service, runner)
    if osutil.IS_WINDOWS:
        return WindowsCredentialStore(service, runner)
    return UnsupportedKeyStore(service, runner)
