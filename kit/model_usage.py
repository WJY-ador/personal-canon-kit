#!/usr/bin/env python3
"""Claude·Codex 로컬 사용량 스캐너 — 대시보드 보드의 모델 패널 데이터 (순수 stdlib).

원천:
  Claude: ~/.claude/projects/**/*.jsonl — assistant 메시지의 model·usage (세션·컨텍스트).
          5h/7d %는 Claude Code /usage와 같은 OAuth usage 조회 재사용 —
          Keychain의 구독 토큰(읽기 전용, 종량제 키 아님·발급 없음). 실패 시 소모량 폴백.
  Codex : ~/.codex/sessions/**/*.jsonl — token_count 이벤트에 rate_limits
          (primary=5h used_percent·resets_at, secondary=7d)와 컨텍스트가 정확값으로 있다.

캐시: (path, mtime, size) 키 인메모리 — 바뀐 파일만 재파싱. 첫 호출만 느리다(~200MB).
"""
import json, re, subprocess, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)

KST = timezone(timedelta(hours=9))
CLAUDE_DIR = Path.home() / ".claude" / "projects"
CODEX_DIR = Path.home() / ".codex" / "sessions"
ACTIVE_MIN = 30          # 이 분 안에 수정된 트랜스크립트 = 활성 세션
_cache = {}              # path -> {"key": (mtime,size), "events": [(ts, model, ctx, out)]}


def _parse_claude_file(p):
    """assistant 메시지만 → (epoch, model, context_tokens, output_tokens) 리스트."""
    events = []
    try:
        with p.open(errors="replace") as f:
            for line in f:
                if '"type":"assistant"' not in line or '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = d.get("message", {})
                u = m.get("usage") or {}
                ts = d.get("timestamp", "")
                try:
                    ep = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
                fresh = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                         + u.get("output_tokens", 0))
                events.append((ep, m.get("model", "?"), ctx, fresh))
    except OSError:
        pass
    return events


def _cached_events(p):
    try:
        st = p.stat()
    except OSError:
        return []
    key = (st.st_mtime, st.st_size)
    c = _cache.get(str(p))
    if c and c["key"] == key:
        return c["events"]
    ev = _parse_claude_file(p)
    _cache[str(p)] = {"key": key, "events": ev}
    return ev


USAGE_CACHE = paths.root() / ".runtime" / ".usage-cache.json"
OAUTH_MIN_INTERVAL = 600    # 조회 최소 간격(초). 5h 쿼터는 10분새 크게 안 변한다
OAUTH_COOLDOWN = 1800       # 429 후 쉬는 시간 — 엔드포인트 제한이 빡빡하다(실측)


def _load_usage_cache():
    try:
        return json.loads(USAGE_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"at": 0, "data": None, "cooldown_until": 0}


_oauth_cache = _load_usage_cache()


def _save_usage_cache():
    try:
        USAGE_CACHE.write_text(json.dumps(_oauth_cache))
    except OSError:
        pass


def _oauth_limits():
    """Claude Code /usage와 동일한 조회. 실패하면 마지막 성공값, 없으면 None.

    쿼터 조회 자체에 레이트리밋이 있다(429 실측 2026-08-26) — 10분 간격을 지키고
    429를 맞으면 30분 쉰다. 강제 새로고침도 이 간격을 넘지 못한다. 마지막 성공값은
    파일(.usage-cache.json)에 남겨 서버를 재시작해도 표시가 끊기지 않는다.
    """
    now_s = time.time()
    if _oauth_cache["data"] and now_s - _oauth_cache["at"] < OAUTH_MIN_INTERVAL:
        return _oauth_cache["data"]
    if now_s < _oauth_cache["cooldown_until"]:
        return _oauth_cache["data"]  # 쿨다운 중엔 마지막 성공값 유지
    try:
        cred = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        tok = json.loads(cred)["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": f"Bearer {tok}",
                     "anthropic-beta": "oauth-2025-04-20"})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        def w(k):
            v = d.get(k) or {}
            ep = None
            if v.get("resets_at"):
                ep = datetime.fromisoformat(v["resets_at"]).timestamp()
            return {"used_percent": v.get("utilization"), "resets_at": ep}
        out = {"h5": w("five_hour"), "d7": w("seven_day")}
        _oauth_cache.update(at=now_s, data=out, cooldown_until=0)
        _save_usage_cache()
        return out
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _oauth_cache["cooldown_until"] = now_s + OAUTH_COOLDOWN
            _save_usage_cache()
        return _oauth_cache["data"]
    except Exception:
        return _oauth_cache["data"]


def claude_usage():
    now = time.time()
    files = [p for p in CLAUDE_DIR.glob("*/*.jsonl")
             if p.stat().st_mtime > now - 8 * 86400]
    h5 = d7 = 0
    sessions = {}
    for p in files:
        for ep, model, ctx, fresh in _cached_events(p):
            if ep > now - 5 * 3600:
                h5 += fresh
            if ep > now - 7 * 86400:
                d7 += fresh
        if p.stat().st_mtime > now - ACTIVE_MIN * 60:
            ev = _cached_events(p)
            if ev:
                ep, model, ctx, fresh = ev[-1]
                proj = p.parent.name.replace(str(Path.home()).replace("/", "-").replace(".", "-") + "-", "~") or "?"
                sessions[p.stem] = {"project": proj, "model": model,
                                    "context": ctx, "last": ep}
    out = {"provider": "Claude", "exact": False,
           "h5_tokens": h5, "d7_tokens": d7,  # 캐시 리드 제외 실소모(입력신규+캐시생성+출력)
           "sessions": sorted(sessions.values(), key=lambda s: -s["last"])[:6]}
    lim = _oauth_limits()
    if lim:
        out.update(exact=True, **lim)
        out["as_of"] = datetime.fromtimestamp(
            _oauth_cache["at"], KST).strftime("%m/%d %H:%M")
    return out


def codex_usage():
    now = time.time()
    files = sorted((p for p in CODEX_DIR.glob("*/*/*/*.jsonl")
                    if p.stat().st_mtime > now - 8 * 86400),
                   key=lambda p: p.stat().st_mtime)
    latest_rl, sessions = None, []
    latest_rl_ts = None
    for p in files:
        last_tc, model = None, "?"
        try:
            with p.open(errors="replace") as f:
                for line in f:
                    if '"model"' in line and model == "?":
                        m = re.search(r'"model":"([^"]+)"', line)
                        if m:
                            model = m.group(1)
                    if '"token_count"' in line:
                        try:
                            last_tc = json.loads(line)
                        except json.JSONDecodeError:
                            pass
        except OSError:
            continue
        if last_tc:
            pay = last_tc.get("payload", {})
            rl = pay.get("rate_limits")
            if rl:
                latest_rl = rl
                ts = last_tc.get("timestamp")
                if ts:
                    try:
                        latest_rl_ts = datetime.fromisoformat(
                            ts.replace("Z", "+00:00")).astimezone(KST).strftime("%m/%d %H:%M")
                    except ValueError:
                        pass
            info = pay.get("info") or {}
            if p.stat().st_mtime > now - ACTIVE_MIN * 60:
                lu = info.get("last_token_usage") or {}
                sessions.append({"project": "codex", "model": model,
                                 "context": lu.get("total_tokens", 0),
                                 "window": info.get("model_context_window"),
                                 "last": p.stat().st_mtime})
    out = {"provider": "Codex", "exact": bool(latest_rl), "sessions": sessions[-6:],
           "as_of": latest_rl_ts}  # 마지막 codex 실행 시점 스냅숏 — 그 뒤 소모는 반영 안 됨
    if latest_rl:
        pri, sec = latest_rl.get("primary") or {}, latest_rl.get("secondary") or {}
        out["h5"] = {"used_percent": pri.get("used_percent"),
                     "resets_at": pri.get("resets_at")}
        out["d7"] = {"used_percent": sec.get("used_percent"),
                     "resets_at": sec.get("resets_at")}
    return out


def snapshot():
    return {"claude": claude_usage(), "codex": codex_usage(),
            "at": datetime.now(KST).strftime("%H:%M:%S")}


if __name__ == "__main__":
    t0 = time.time()
    print(json.dumps(snapshot(), ensure_ascii=False, indent=1)[:2500])
    print(f"... scan {time.time()-t0:.1f}s")
