"""전송 대기 파일 → POST /api/imports. 재시도 규칙:

- 항상 저장해 둔 바이트와 멱등 키를 그대로 다시 보낸다(요청을 다시 만들지 않는다, 모델 재실행 없음).
- 200/201            → accepted(대기 파일 삭제)
- 409 IDEMPOTENCY-CONFLICT·KIND-CHANGED → conflict + 격리(같은 키에 다른 본문 / 원본 종류 변경)
- 409 SOURCE-REVISION-CONFLICT·기타 409 → halted(그 원본만 멈춤, 사용자 확인 필요)
- 400/413/415/422 · 403 POLICY-DENIED → rejected + 격리(계약·장치 정책 불일치)
- 401 · 403 AUTH-BLOCKED · 409 DEVICE-REVOKED → 전체 멈춤(auth)
- 403 STREAM-MISMATCH → 전체 멈춤(config) · 403 NAME-REQUIRED → (account) · 409 POLICY-STALE → (policy)
  · 그 밖의 403 → (forbidden). 멈춤은 사용자 조치 뒤 `run-once --resume <범위>` 로 푼다
- 429 IMPORT-QUOTA·PEOPLE-QUOTA → 본문 resume_at(없거나 이상하면 다음 KST 0시) · INBOX-FULL → 1시간 · 그 밖(RATE-LIMIT) → Retry-After(없으면 60초). 이번 실행 중단
- 5xx(*-OFF 스위치 꺼짐 포함)·네트워크·시간 초과 → 대기 유지, 지수 대기(최대 1시간). 이번 실행 중단
로그·오류 메시지엔 상태 코드·규칙 코드만 남긴다(본문·토큰 없음).
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.error
import urllib.request

from . import osutil

IMPORTS_PATH = "/api/imports"
DEVICE_PATH = "/api/imports/device"
DEVICE_REVOKE_PATH = "/api/imports/device/revoke"
# 서버 server/imports.py _HTTP 표 기준
DAILY_LIMIT_CODES = ("IMPORT-QUOTA", "PEOPLE-QUOTA")   # 본문 resume_at(다음 KST 0시) 이 있으면 그 시각
AUTH_CODES = ("AUTH-INVALID", "TOKEN-EXPIRED", "TOKEN-REVOKED", "AUTH-BLOCKED", "DEVICE-REVOKED")
GLOBAL_HALT_CODES = {"STREAM-MISMATCH": "config", "NAME-REQUIRED": "account", "POLICY-STALE": "policy"}
CONFLICT_CODES = ("IDEMPOTENCY-CONFLICT", "KIND-CHANGED")
UPLOAD_HALT_SCOPES = ("auth", "config", "account", "policy", "forbidden")
REJECT_STATUSES = (400, 413, 415, 422)
MAX_RESPONSE_BYTES = 64 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가지 않는다 — 토큰이 다른 주소로 가지 않게."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    return urllib.request.build_opener(_NoRedirect())


def _parse_iso(s):
    try:
        return _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def next_kst_midnight(now: _dt.datetime) -> _dt.datetime:
    n = now.astimezone(osutil.KST)
    return (n + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def resume_at_or_midnight(payload: dict, now: _dt.datetime) -> _dt.datetime:
    """서버 resume_at(오프셋 있는 ISO) 을 쓰되, 과거·48시간 넘는 미래·형식 오류면 다음 KST 0시."""
    ra = _parse_iso(payload.get("resume_at")) if isinstance(payload.get("resume_at"), str) else None
    if ra is not None and ra.tzinfo is not None and now < ra <= now + _dt.timedelta(hours=48):
        return ra
    return next_kst_midnight(now)


class DeviceError(RuntimeError):
    """장치 경로 실패. status·code 만 담는다(토큰·본문 없음)."""

    def __init__(self, kind: str, status: int | None = None, code: str = ""):
        self.kind, self.status, self.code = kind, status, code
        super().__init__(f"{kind}" + (f" http-{status}" if status else "") + (f" {code}" if code else ""))


class Uploader:
    def __init__(self, server_url: str, token: str, *, timeout: float = 30, opener=None,
                 sleep=time.sleep, now=osutil.kst_now):
        self.base = server_url.rstrip("/")
        self.url = self.base + IMPORTS_PATH
        self._token = token
        self.timeout = timeout
        self.opener = opener or _opener()
        self.sleep = sleep
        self.now = now

    def __repr__(self):
        return "Uploader(<redacted>)"

    def post(self, idem_key: str, body: bytes):
        return self._request("POST", self.url, body, {"Idempotency-Key": idem_key,
                                                      "Content-Type": "application/json"})

    def _request(self, method: str, url: str, body: bytes | None, extra: dict):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("Accept", "application/json")
        for k, v in extra.items():
            req.add_header(k, v)
        try:
            resp = self.opener.open(req, timeout=self.timeout)
            status, headers, data = resp.status, resp.headers, resp.read(MAX_RESPONSE_BYTES)
            resp.close()
        except urllib.error.HTTPError as e:
            status, headers = e.code, e.headers
            try:
                data = e.read(MAX_RESPONSE_BYTES)
            except Exception:
                data = b""
            e.close()
        try:
            payload = json.loads(data.decode("utf-8")) if data else {}
        except (UnicodeDecodeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return status, headers, payload

    def _device_call(self, method: str, path: str) -> dict:
        try:
            status, _h, payload = self._request(method, self.base + path, b"" if method == "POST" else None, {})
        except (urllib.error.URLError, OSError, TimeoutError):
            raise DeviceError("network") from None
        if status != 200:
            code = payload.get("code") if isinstance(payload.get("code"), str) else ""
            raise DeviceError("auth" if status == 401 else "server", status, code)
        return payload

    def device(self) -> dict:
        """GET /api/imports/device → {stream_id, label, policy, policy_revision, hmac_namespace, token_expires_at}"""
        return self._device_call("GET", DEVICE_PATH)

    def revoke(self) -> dict:
        """POST /api/imports/device/revoke → {stream_id, revoked: true}. 이후 이 토큰은 401 TOKEN-REVOKED."""
        return self._device_call("POST", DEVICE_REVOKE_PATH)

    def run(self, store, *, max_n: int = 100, min_interval: float = 0.0) -> dict:
        out = {"sent": 0, "accepted": 0, "duplicate": 0, "conflict": 0, "halted": 0, "rejected": 0,
               "deferred": 0, "stopped": None}
        for scope in UPLOAD_HALT_SCOPES:
            if store.halted(scope):
                out["stopped"] = scope + "-halted"
                return out
        now = self.now()
        first = True
        for rev in store.pending_revisions():
            if out["sent"] >= max_n:
                out["stopped"] = "max-per-run"
                break
            nxt = _parse_iso(rev["next_attempt_at"]) if rev["next_attempt_at"] else None
            if nxt is not None and nxt > now:
                out["deferred"] += 1
                continue
            # 같은 원본의 앞 개정이 아직 열려 있으면 뒤 개정을 보내지 않는다(부모 순서 유지)
            earlier = store.conn.execute(
                "SELECT 1 FROM revisions WHERE original_id=? AND seq<? AND status IN ('pending','halted') LIMIT 1",
                (rev["original_id"], rev["seq"])).fetchone()
            if earlier:
                out["deferred"] += 1
                continue
            if not first and min_interval > 0:
                self.sleep(min_interval)
            first = False
            key = rev["idempotency_key"]
            body = store.read_pending(rev)
            out["sent"] += 1
            try:
                status, headers, payload = self.post(key, body)
            except (urllib.error.URLError, OSError, TimeoutError):
                self._backoff(store, rev, "network")
                out["stopped"] = "network"
                break
            code = payload.get("code") if isinstance(payload.get("code"), str) else ""
            if status in (200, 201):
                store.mark(key, "accepted", result=payload)
                out["accepted"] += 1
                if status == 200 or payload.get("duplicate") is True:
                    out["duplicate"] += 1
            elif status == 401 or code in AUTH_CODES:
                store.halt("auth", f"http-{status}")
                store.bump_attempt(key, f"http-{status}", None)
                out["stopped"] = "auth"
                break
            elif code in GLOBAL_HALT_CODES:
                store.halt(GLOBAL_HALT_CODES[code], code)
                store.bump_attempt(key, f"http-{status}:{code}", None)
                out["stopped"] = GLOBAL_HALT_CODES[code]
                break
            elif status == 409 and code in CONFLICT_CODES:
                store.mark(key, "conflict", error=code)
                store.quarantine("upload", code.lower(), key)
                out["conflict"] += 1
            elif status == 409:
                store.mark(key, "halted", error=code or "http-409")
                out["halted"] += 1
            elif status == 403 and code == "POLICY-DENIED":
                store.mark(key, "rejected", error=code)
                store.quarantine("upload", "policy-denied", key)
                out["rejected"] += 1
            elif status == 403:
                store.halt("forbidden", code or "http-403")
                store.bump_attempt(key, f"http-403:{code}" if code else "http-403", None)
                out["stopped"] = "forbidden"
                break
            elif status == 429:
                if code in DAILY_LIMIT_CODES:
                    at = resume_at_or_midnight(payload, self.now())
                elif code == "INBOX-FULL":
                    at = self.now() + _dt.timedelta(hours=1)
                else:
                    ra = headers.get("Retry-After") if headers is not None else None
                    sec = int(ra) if ra and str(ra).isdigit() else 60
                    at = self.now() + _dt.timedelta(seconds=min(sec, 3600))
                store.bump_attempt(key, f"http-429:{code}" if code else "http-429", at.isoformat(timespec="seconds"))
                out["stopped"] = "rate-limit"
                break
            elif status in REJECT_STATUSES:
                store.mark(key, "rejected", error=f"http-{status}:{code}" if code else f"http-{status}")
                store.quarantine("upload", f"rejected-{status}", key)
                out["rejected"] += 1
            else:
                self._backoff(store, rev, f"http-{status}")
                out["stopped"] = "server"
                break
        return out

    def _backoff(self, store, rev, err: str) -> None:
        n = (rev["attempts"] or 0) + 1
        wait = min(60 * (2 ** min(n, 6)), 3600)
        at = self.now() + _dt.timedelta(seconds=wait)
        store.bump_attempt(rev["idempotency_key"], err, at.isoformat(timespec="seconds"))
