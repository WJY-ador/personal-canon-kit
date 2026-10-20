"""일회 실행 — 수집기 산출물 → 어댑터 → PC 원문·신원 매핑 → 결정적 변환 → 계약 검증
→ 전송 대기 파일(원자적) → 업로드 → 접수 결과 저장.
"""
from __future__ import annotations

import json
import random
import time

from . import adapters as _adapters
from . import config as _config
from . import contract, hmacid, jcs, keystore, store as _store
from .adapters.base import Observation, Quarantine


class RunError(RuntimeError):
    pass


def _projection(fields: dict, transform_version: str) -> str:
    """원본 내용의 지문 — 바뀌면 새 개정. 정책 판(policy_revision)은 요청 메타데이터라 넣지 않는다:
    허용 규칙이 바뀌어도 이미 접수된 원본은 새 개정을 만들지 않는다(메인 판정 2026-10-20)."""
    return jcs.sha256_hex({"fields": fields, "transform_version": transform_version})


def stage_activity(st: "_store.Store", cfg: dict, adapter, obs: Observation, hmac_key: bytes | None,
                   counts: dict) -> None:
    ev = st.event_get(adapter.name, obs.dedupe_key)
    if ev is None:
        ev = st.event_create(adapter.name, obs.dedupe_key, adapter.kind, obs.fingerprint, raw_obj=obs.raw)
        counts["new_events"] += 1
    elif ev["fingerprint"] != obs.fingerprint:
        # 같은 중복 기준인데 신원이 달라짐 → 판별 불가, 격리하고 새 개정 안 만듦
        st.quarantine(adapter.name, "identity-changed", obs.dedupe_key)
        counts["quarantined"] += 1
        return
    else:
        try:
            st.raw_update(ev["raw_id"], obs.raw)
        except _store.StoreError:
            pass

    people = []
    if obs.people:
        ns = cfg.get("hmac_namespace")
        if not ns or hmac_key is None:
            raise RunError("hmac key/namespace not configured")
        for provider_code, account_id, role in obs.people:
            h = st.person_hmac(ns, provider_code, account_id,
                               lambda: hmacid.person_hmac(hmac_key, provider_code, account_id))
            people.append({"hmac": h, "role": role})
    fields = {
        "action": obs.action,
        "state": obs.state,
        "occurred_on": obs.occurred_on,
        "observed_on": obs.observed_on,
        "provider": obs.provider,
        "raw_id": ev["raw_id"],
        "people": people,
        "axes": list(obs.axes),
    }
    _stage(st, cfg, ev["original_id"], "activity", adapter.transform_version, "unattended"
           if cfg.get("_execution") == "unattended" else "attended", fields, counts, adapter.name)


def _stage(st, cfg, original_id, kind, transform_version, execution, fields, counts, adapter_name) -> None:
    pr = cfg["policy_revision"]
    proj = _projection(fields, transform_version)
    latest = st.latest_revision(original_id)
    if latest is not None and latest["projection_sha"] == proj:
        # 같은 내용: 접수·대기면 변경 없음, 거절·충돌이면 같은 요청을 다시 만들지 않는다
        counts["unchanged" if latest["status"] in ("pending", "accepted", "halted") else "blocked"] += 1
        return
    if st.has_open_revision(original_id):
        counts["waiting"] += 1
        return
    accepted = st.latest_accepted(original_id)
    prev = accepted["revision"] if accepted is not None else None
    if accepted is not None and accepted["projection_sha"] == proj:
        counts["unchanged"] += 1
        return
    rev = _store.new_revision()
    obj = contract.build_request(
        kind=kind, source_instance_id=cfg["source_instance_id"], original_id=original_id, revision=rev,
        previous_revision=prev, execution=execution, transform_version=transform_version,
        policy_revision=pr, fields=fields)
    try:
        body = contract.encode_request(obj, policy=_config.policy_of(cfg))
    except contract.ContractError as e:
        st.quarantine(adapter_name, "contract:" + e.code, original_id)
        counts["quarantined"] += 1
        return
    key = contract.idempotency_key(cfg["source_instance_id"], original_id, rev)
    st.add_revision(original_id=original_id, revision=rev, previous_revision=prev,
                    projection_sha=proj, idem_key=key, body=body)
    counts["staged"] += 1


def stage_self_text(st, cfg, *, adapter_name: str, dedupe_key: str, fingerprint: str, kind: str,
                    transform_version: str, execution: str, fields: dict, counts: dict) -> bool:
    """자기 글·요약 요청 생성. 로컬 추가 대조(규칙 7): 매핑에 있는 계정 식별자가 본문에 있으면 격리."""
    text = " ".join(str(fields.get(k) or "") for k in ("title", "body"))
    for acct in st.known_account_ids():
        if len(acct) >= 3 and acct in text:
            st.quarantine(adapter_name, "local-identity-match", dedupe_key)
            counts["quarantined"] += 1
            return False
    ev = st.event_get(adapter_name, dedupe_key)
    if ev is None:
        ev = st.event_create(adapter_name, dedupe_key, kind, fingerprint, raw_obj=None)
        counts["new_events"] += 1
    before = counts["staged"]
    _stage(st, cfg, ev["original_id"], kind, transform_version, execution, fields, counts, adapter_name)
    return counts["staged"] > before


def refresh_policy(paths, st, cfg: dict, up) -> dict:
    """업로드가 POLICY-STALE 로 멈췄을 때 1회 — 같은 토큰으로 GET /api/imports/device 를 불러 허용 규칙을 다시 받고,
    설정을 갱신한 뒤 아직 접수되지 않은 전송 대기 요청을 새 정책 판으로 다시 만든다(토큰 재입력 없음).
    입력원·키 공간이 바뀌었으면 고치지 않는다(connect 를 다시 해야 한다). 반환 {"status", ...} — 값·토큰 없음."""
    from .uploader import DeviceError
    try:
        info = up.device()
    except DeviceError as e:
        return {"status": "device-" + e.kind}
    public = {k: v for k, v in cfg.items() if not k.startswith("_")}
    try:
        new = _config.apply_device_info(public, info, expected_stream=cfg.get("source_instance_id"))
    except _config.ConfigError:
        return {"status": "device-mismatch"}
    if new.get("hmac_namespace") != cfg.get("hmac_namespace"):
        return {"status": "namespace-changed"}
    if new.get("policy_revision") == cfg.get("policy_revision"):
        return {"status": "unchanged"}         # 서버 판이 그대로인데 POLICY-STALE — 고칠 것이 없다. 멈춤 유지
    try:
        _config.validate_config(new)
    except _config.ConfigError:
        return {"status": "config-invalid"}    # 예: 설정의 likes.axes 가 새 허용 축 밖
    _config.save(paths, new)
    pr, pol = new["policy_revision"], _config.policy_of(new)

    def rebuild(body: bytes):
        obj = json.loads(body.decode("utf-8"))
        obj["policy_revision"] = pr
        fields = {k: v for k, v in obj.items() if k not in contract.COMMON_FIELDS}
        return contract.encode_request(obj, policy=pol), _projection(fields, obj["transform_version"])
    out = st.rebase_pending(rebuild)
    out.update({"status": "ok", "policy_revision": pr})
    cfg.update(new)
    return out


def load_secrets(cfg: dict, ks: keystore.KeyStore, need_hmac: bool):
    sid = cfg.get("source_instance_id")
    if not sid or not cfg.get("server_url"):
        raise RunError("not connected — run `connect` first")
    token = ks.get(_config.token_account(sid))
    if not token:
        raise RunError("upload token missing in keychain — run `connect` again")
    hkey = None
    if need_hmac:
        ns = cfg.get("hmac_namespace")
        if not ns:
            raise RunError("hmac_namespace not configured")
        hk = ks.get(_config.hmac_account(ns))
        if not hk:
            raise RunError("hmac key missing in keychain for this namespace")
        hkey = hmacid.key_from_hex(hk)
    return token, hkey


def collect(st, cfg: dict, hmac_key, counts: dict) -> None:
    for name, section in (cfg.get("adapters") or {}).items():
        if not section.get("enabled"):
            continue
        adapter = _adapters.get(name)            # 차단 입력원은 여기서 TransmissionBlocked
        adapter.check_config(section)            # 골격은 여기서 AdapterNotActive
        for item in adapter.collect(section):
            if isinstance(item, Quarantine):
                st.quarantine(name, item.reason, item.key)
                counts["quarantined"] += 1
            else:
                counts["observed"] += 1
                stage_activity(st, cfg, adapter, item, hmac_key, counts)


def new_counts() -> dict:
    return {"observed": 0, "new_events": 0, "staged": 0, "unchanged": 0, "waiting": 0, "blocked": 0,
            "quarantined": 0}


def run_once(paths, *, ks: keystore.KeyStore, uploader_factory=None, summarizer=None,
             scheduled: bool = False, jitter_max: float = 300.0, sleep=time.sleep, upload: bool = True) -> dict:
    """잠금 → (예약 실행이면 0~5분 지연) → 수집·변환·대기 저장 → 요약(켜졌을 때) → 업로드."""
    from . import osutil
    from .uploader import Uploader

    paths.ensure()
    lock = osutil.FileLock(paths.lock("run"))
    try:
        lock.acquire()
    except osutil.LockBusy:
        return {"skipped": "already-running"}
    try:
        if scheduled and jitter_max > 0:
            sleep(random.uniform(0, jitter_max))
        cfg = _config.load(paths)
        cfg = dict(cfg)
        cfg["_execution"] = "unattended" if scheduled else "attended"
        st = _store.Store(paths)
        try:
            st.reconcile()
            need_hmac = any(s.get("enabled") for s in (cfg.get("adapters") or {}).values())
            token, hkey = load_secrets(cfg, ks, need_hmac)
            counts = new_counts()
            collect(st, cfg, hkey, counts)
            result = {"collect": counts}
            if summarizer is not None:
                result["summary"] = summarizer(st, cfg)
            if upload:
                up = (uploader_factory or Uploader)(cfg["server_url"], token,
                                                    timeout=cfg["upload"]["timeout_sec"])
                res_up = up.run(st, max_n=int(cfg["upload"]["max_per_run"]),
                                min_interval=float(cfg["upload"]["min_interval_sec"]))
                if (res_up.get("stopped") or "").replace("-halted", "") == "policy":
                    # 허용 규칙이 바뀜 → 정책을 다시 받아 대기 요청을 고친 뒤 이번 실행에서 한 번만 다시 보낸다
                    ref = refresh_policy(paths, st, cfg, up)
                    result["policy_refresh"] = ref
                    if ref["status"] == "ok":
                        st.clear_halt("policy")
                        res_up = up.run(st, max_n=int(cfg["upload"]["max_per_run"]),
                                        min_interval=float(cfg["upload"]["min_interval_sec"]))
                result["upload"] = res_up
            result["state"] = st.counts()
            return result
        finally:
            st.close()
    finally:
        lock.release()
