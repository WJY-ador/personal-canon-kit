"""CLI — connect · validate · run-once · status · schedule install/remove · serve(원문 뷰어·--jobs 작업 실행) · disconnect.

사용: python3 -m agent [--home 경로] <명령> ...
출력엔 토큰·HMAC 키·원문·실명·URL 을 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from . import AGENT_VERSION, config as _config, contract, hmacid, keystore, osutil, runner, schedule, store as _store

EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_HALT = 0, 1, 2, 3


class Ctx:
    def __init__(self, home, *, ks_factory=None, getpass_fn=None, uploader_factory=None,
                 schedule_runner=None, launch_agents_dir=None, exec_runner=None, out=None, stdin=None,
                 jobs_adapter_factory=None, jobs_client_factory=None, jobs_serve_kw=None):
        self.paths = _config.Paths(home)
        self.ks_factory = ks_factory or keystore.for_platform
        self.getpass_fn = getpass_fn or getpass.getpass
        self.uploader_factory = uploader_factory
        self.schedule_runner = schedule_runner
        self.launch_agents_dir = launch_agents_dir
        self.exec_runner = exec_runner
        self.jobs_adapter_factory = jobs_adapter_factory    # (runner) → jobs 어댑터 — 시험은 fake
        self.jobs_client_factory = jobs_client_factory      # (server, token) → jobs.Client
        self.jobs_serve_kw = dict(jobs_serve_kw or {})      # 시험용 serve_jobs 인자(유예 등)
        self.out = out or sys.stdout
        self.stdin = stdin or sys.stdin

    def say(self, msg: str) -> None:
        print(msg, file=self.out)

    def keystore(self, cfg):
        return self.ks_factory(cfg.get("keychain_service") or _config.KEYCHAIN_SERVICE)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m agent", description="my-crm 로컬 에이전트 " + AGENT_VERSION)
    p.add_argument("--home", help="에이전트 홈(기본: OS 사용자 디렉터리)")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("connect", help="장치 연결 — 업로드 토큰을 Keychain 에 넣는다")
    c.add_argument("--server", required=True, help="서버 주소(https)")
    c.add_argument("--label", help="(덮어쓰기) 장치 이름 — 기본은 서버 값")
    c.add_argument("--hmac-namespace", help="(덮어쓰기) 키 공간 ID — 기본은 서버 값")
    c.add_argument("--policy-revision", type=int, help="(덮어쓰기) 정책 판 — 기본은 서버 값")
    c.add_argument("--axis", action="append", default=[], help="(덮어쓰기) 허용 축 항목 ID(여러 번)")
    c.add_argument("--token-stdin", action="store_true", help="토큰을 표준 입력 한 줄로 받는다(화면 표시 없음)")

    v = sub.add_parser("validate", help="설정·전송 대기 파일·지정 파일의 계약 검증")
    v.add_argument("files", nargs="*")

    r = sub.add_parser("run-once", help="수집·변환·검증·업로드 1회")
    r.add_argument("--scheduled", action="store_true", help="예약 실행(0~5분 지연, unattended)")
    r.add_argument("--no-upload", action="store_true", help="전송 대기까지만")
    r.add_argument("--resume", choices=("auth", "config", "account", "policy", "forbidden", "summary"),
                   help="사용자 조치 뒤 멈춤 해제")

    sub.add_parser("status", help="연결·대기·격리·예약 상태")

    s = sub.add_parser("schedule", help="launchd 예약")
    s.add_argument("action", choices=("install", "remove"))

    sv = sub.add_parser("serve", help="원문 뷰어(127.0.0.1) · --jobs = 작업 실행(상주)")
    sv.add_argument("--port", type=int)
    sv.add_argument("--jobs", action="store_true", help="서버 작업을 받아 이 PC 의 claude -p 로 실행(상주, 종료 = SIGTERM·Ctrl+C)")

    d = sub.add_parser("disconnect", help="서버 토큰 폐기 + 로컬 업로드 토큰 제거")
    d.add_argument("--local-only", action="store_true", help="서버 폐기를 건너뛰고 로컬만 지운다")
    d.add_argument("--forget-hmac-key", action="store_true",
                   help="이 키 공간의 HMAC 키도 지운다(같은 사람 연결이 끊긴다)")
    return p


# ---- 명령 ---------------------------------------------------------------------
def _uploader(ctx: Ctx, server: str, token: str):
    from .uploader import Uploader
    return (ctx.uploader_factory or Uploader)(server, token, timeout=30)


def cmd_connect(ctx: Ctx, a) -> int:
    """토큰만 받는다. 입력원·정책·키 공간은 GET /api/imports/device 로 받아 확인한다.
    --hmac-namespace·--policy-revision·--axis·--label 은 서버 값을 덮어쓸 때만 쓴다."""
    from .uploader import DeviceError
    server = _config.check_server_url(a.server)
    if a.hmac_namespace is not None and not _config.NAMESPACE_RE.match(a.hmac_namespace):
        ctx.say("키 공간 ID 형식이 맞지 않아요.")
        return EXIT_USAGE
    if a.policy_revision is not None and a.policy_revision < 1:
        ctx.say("정책 판은 1 이상이에요.")
        return EXIT_USAGE
    for ax in a.axis:
        if not contract.AXIS_ID_RE.match(ax):
            ctx.say("축 항목 ID 형식이 맞지 않아요.")
            return EXIT_USAGE
    if a.label is not None and not (1 <= len(a.label) <= 80):
        ctx.say("장치 이름은 1~80자예요.")
        return EXIT_USAGE
    if a.token_stdin:
        token = ctx.stdin.readline().strip()
    else:
        token = ctx.getpass_fn("업로드 토큰(화면에 안 보임): ").strip()
    try:
        stream_id, _tid = contract.parse_token(token)
    except contract.ContractError:
        ctx.say("토큰 형식이 맞지 않아요. 서버 연결 장치 화면의 토큰을 그대로 붙여 넣으세요.")
        return EXIT_USAGE
    # 1) 서버 확인 — 실패하면 아무것도 저장하지 않는다
    try:
        info = _uploader(ctx, server, token).device()
    except DeviceError as e:
        if e.kind == "auth":
            ctx.say(f"서버가 토큰을 받지 않았어요({e.code or e.status}). 연결 장치 화면에서 토큰을 다시 발급하세요.")
        elif e.kind == "network":
            ctx.say("서버에 닿지 못했어요. 주소·네트워크를 확인하세요.")
        else:
            ctx.say(f"서버 확인 실패: http-{e.status} {e.code}".rstrip())
        return EXIT_ERR
    ctx.paths.ensure()
    cfg = _config.load(ctx.paths)
    try:
        new = _config.apply_device_info(cfg, info, expected_stream=stream_id)
    except _config.ConfigError as e:
        ctx.say(f"서버 장치 정보가 맞지 않아요: {e}")
        return EXIT_ERR
    overrides = []
    if a.hmac_namespace is not None and a.hmac_namespace != new["hmac_namespace"]:
        new["hmac_namespace"] = a.hmac_namespace
        overrides.append("hmac_namespace")
    if a.policy_revision is not None and a.policy_revision != new["policy_revision"]:
        new["policy_revision"] = a.policy_revision
        overrides.append("policy_revision")
    if a.axis:
        new["allowed_axes"] = list(dict.fromkeys(a.axis))
        overrides.append("allowed_axes")
    if a.label is not None:
        new["device_label"] = a.label
    new["server_url"] = server
    # 2) 자격 증명 저장
    ks = ctx.keystore(cfg)
    old_sid = cfg.get("source_instance_id")
    try:
        ks.set(_config.token_account(stream_id), token)
        hk_acct = _config.hmac_account(new["hmac_namespace"])
        made_key = False
        if not ks.get(hk_acct):
            ks.set(hk_acct, hmacid.new_key_hex())
            made_key = True
        if old_sid and old_sid != stream_id:
            ks.delete(_config.token_account(old_sid))
    except keystore.KeyStoreError as e:
        ctx.say(f"자격 증명 저장소에 쓰지 못했어요: {e}. 평문으로 저장하지 않고 멈춥니다.")
        return EXIT_ERR
    try:
        _config.save(ctx.paths, new)
    except _config.ConfigError as e:
        ctx.say(f"설정 오류: {e}")
        return EXIT_ERR
    ctx.say("연결했어요(서버 확인 완료).")
    ctx.say(f"  입력원 ID: {stream_id}")
    ctx.say(f"  정책 판: {new['policy_revision']} · 허용 행동: {', '.join(new['allowed_actions'] or []) or '없음'}"
            f" · 허용 축 {len(new['allowed_axes'])}개")
    ctx.say(f"  토큰 만료: {new.get('token_expires_at') or '모름'}")
    ctx.say("  업로드 토큰: Keychain 에 저장(표시 안 함)")
    ctx.say("  HMAC 키: " + ("새로 만들어 Keychain 에 저장" if made_key else "기존 키 사용"))
    if overrides:
        ctx.say("  서버 값을 덮어씀: " + ", ".join(overrides))
    ctx.say(f"  설정 파일: {ctx.paths.config}")
    return EXIT_OK


def cmd_validate(ctx: Ctx, a) -> int:
    bad = 0
    try:
        cfg = _config.load(ctx.paths)
    except _config.ConfigError as e:
        ctx.say(f"설정 오류: {e}")
        return EXIT_ERR
    pol = _config.policy_of(cfg) if cfg.get("source_instance_id") else None
    targets = []
    if os.path.isdir(ctx.paths.pending):
        targets += [("pending/" + n, os.path.join(ctx.paths.pending, n))
                    for n in sorted(os.listdir(ctx.paths.pending)) if n.endswith(".json")]
    targets += [(f, f) for f in a.files]
    for label, path in targets:
        try:
            with open(path, "rb") as f:
                contract.validate_bytes(f.read(), policy=pol)
            ctx.say(f"OK   {os.path.basename(label)}")
        except contract.ContractError as e:
            bad += 1
            ctx.say(f"FAIL {os.path.basename(label)} {e.code} {e.field}")
        except OSError:
            bad += 1
            ctx.say(f"FAIL {os.path.basename(label)} UNREADABLE")
    ctx.say(f"설정 OK · 검사 {len(targets)}건 · 실패 {bad}건")
    return EXIT_OK if bad == 0 else EXIT_ERR


def cmd_run_once(ctx: Ctx, a) -> int:
    try:
        cfg = _config.load(ctx.paths)
    except _config.ConfigError as e:
        ctx.say(f"설정 오류: {e}")
        return EXIT_ERR
    if a.resume:
        st = _store.Store(ctx.paths)
        try:
            ok = st.clear_halt(a.resume)
        finally:
            st.close()
        ctx.say(f"멈춤 해제: {a.resume}" if ok else f"멈춤 없음: {a.resume}")
    ks = ctx.keystore(cfg)

    def summarizer(st, c):
        from . import summary
        return summary.summarize(st, c, paths=ctx.paths, exec_runner=ctx.exec_runner)

    from .adapters import AdapterError
    try:
        res = runner.run_once(ctx.paths, ks=ks, uploader_factory=ctx.uploader_factory, summarizer=summarizer,
                              scheduled=a.scheduled, upload=not a.no_upload)
    except (runner.RunError, AdapterError, keystore.KeyStoreError, _config.ConfigError) as e:
        ctx.say(f"실행 멈춤: {e}")
        return EXIT_ERR
    except Exception as e:  # 요약 단계 설정 오류 등 — 값 없이 종류만
        ctx.say(f"실행 멈춤: {type(e).__name__}")
        return EXIT_ERR
    if res.get("skipped"):
        ctx.say("이미 실행 중이라 건너뛰었어요.")
        return EXIT_OK
    ctx.say(json.dumps({k: v for k, v in res.items()}, ensure_ascii=False, sort_keys=True))
    up = res.get("upload") or {}
    stopped = (up.get("stopped") or "").replace("-halted", "")
    hints = {
        "auth": "서버가 토큰을 거절했어요. 웹 계정 메뉴 › 연결 장치에서 토큰을 다시 발급해 connect 한 뒤",
        "config": "입력원 ID 가 토큰과 달라요. connect 를 다시 한 뒤",
        "account": "웹에서 표시 이름을 먼저 정한 뒤",
        "policy": "장치 허용 규칙이 바뀌었는데 자동으로 다시 받지 못했어요(policy_refresh 참고). connect 를 다시 한 뒤",
        "forbidden": "서버가 요청을 막았어요. 연결 장치 화면을 확인한 뒤",
    }
    if stopped in hints:
        ctx.say(f"{hints[stopped]} `run-once --resume {stopped}` 로 다시 시작하세요.")
        return EXIT_HALT
    return EXIT_OK


def cmd_status(ctx: Ctx, a) -> int:
    try:
        cfg = _config.load(ctx.paths)
    except _config.ConfigError as e:
        ctx.say(f"설정 오류: {e}")
        return EXIT_ERR
    connected = bool(cfg.get("source_instance_id"))
    ctx.say(f"홈: {ctx.paths.home}")
    ctx.say(f"연결: {'예' if connected else '아니오'}" + (f" · 입력원 {cfg['source_instance_id']}" if connected else ""))
    if connected:
        try:
            ks = ctx.keystore(cfg)
            has_tok = bool(ks.get(_config.token_account(cfg["source_instance_id"])))
            has_key = bool(ks.get(_config.hmac_account(cfg["hmac_namespace"]))) if cfg.get("hmac_namespace") else False
            ctx.say(f"업로드 토큰: {'있음' if has_tok else '없음'} · HMAC 키: {'있음' if has_key else '없음'}")
            ctx.say(f"토큰 만료: {cfg.get('token_expires_at') or '모름'} · 정책 판 {cfg.get('policy_revision')}")
        except keystore.KeyStoreError as e:
            ctx.say(f"자격 증명 저장소 접근 불가: {e}")
    en = [n for n, s in (cfg.get("adapters") or {}).items() if s.get("enabled")]
    ctx.say("켜진 어댑터: " + (", ".join(en) if en else "없음"))
    ctx.say("무인 요약: " + ("켜짐" if (cfg.get("summary") or {}).get("enabled") else "꺼짐"))
    if osutil.IS_MAC:
        ctx.say("예약: " + ("등록됨" if schedule.installed(ctx.launch_agents_dir) else "없음"))
    if os.path.exists(ctx.paths.db):
        st = _store.Store(ctx.paths)
        try:
            c = st.counts()
            ctx.say(f"이벤트 {c['events']} · 대기 {c['pending']} · 접수 {c['accepted']} · 멈춤 {c['halted']} · "
                    f"충돌 {c['conflict']} · 거절 {c['rejected']} · 삭제됨 {c['deleted']} · 격리 {c['quarantined']} · 사람 {c['people']}")
            ctx.say(f"오늘 요약 시도: {st.summary_attempts_today(osutil.kst_today())}")
            if c["halts"]:
                ctx.say("멈춤 범위: " + ", ".join(c["halts"]))
        finally:
            st.close()
    return EXIT_OK


def cmd_schedule(ctx: Ctx, a) -> int:
    try:
        if a.action == "install":
            r = schedule.install(home=ctx.paths.home, launch_agents_dir=ctx.launch_agents_dir,
                                 runner=ctx.schedule_runner)
            ctx.say({"installed": "예약을 등록했어요(1시간마다).", "unchanged": "이미 같은 예약이 있어요."}[r])
        else:
            r = schedule.remove(launch_agents_dir=ctx.launch_agents_dir, runner=ctx.schedule_runner)
            ctx.say({"removed": "예약을 해제했어요. 원문·대응표는 그대로예요.", "absent": "등록된 예약이 없어요."}[r])
    except schedule.ScheduleError as e:
        ctx.say(f"예약 실패: {e}")
        return EXIT_ERR
    return EXIT_OK


def cmd_serve(ctx: Ctx, a, *, serve_forever=True):
    if getattr(a, "jobs", False):
        return cmd_serve_jobs(ctx, a)
    from . import viewer
    cfg = _config.load(ctx.paths)
    port = a.port or cfg["viewer_port"]
    srv = viewer.make_server(port=port, resolve_raw=viewer.store_resolver(ctx.paths))
    ctx.say(f"원문 뷰어: http://{viewer.BIND_HOST}:{srv.server_address[1]}/raw/<id> (Ctrl+C 로 종료)")
    if not serve_forever:
        return srv
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return EXIT_OK


def cmd_serve_jobs(ctx: Ctx, a) -> int:
    """작업 실행 상주(③-0.1). 잠금 → 장치 인증 → 롱폴·실행. 출력 = 코드만 담은 JSON 줄(jobs._log_line) — 본문·토큰·주소·예외 메시지 없음.
    exit: 신호로 멈춤 0 · 설정·잠금 충돌·기타 1 · 인증(토큰 없음·거절·폐기) 3."""
    from . import jobs
    from .uploader import AUTH_CODES, DeviceError

    def log(line: str) -> None:
        ctx.say(line)
        flush = getattr(ctx.out, "flush", None)
        if flush:
            flush()

    def stop(why: str, err=None, code=EXIT_ERR) -> int:
        log(jobs._log_line({"event": "start", "stopped": why, "error": type(err).__name__ if err else None}))
        return code

    try:
        cfg = _config.load(ctx.paths)
    except _config.ConfigError as e:
        return stop("config", e)
    sid, server = cfg.get("source_instance_id"), cfg.get("server_url")
    if not sid or not server:
        return stop("not-connected")
    ctx.paths.ensure()
    try:
        token = ctx.keystore(cfg).get(_config.token_account(sid))
    except keystore.KeyStoreError as e:
        return stop("keystore", e)
    if not token:
        return stop("auth", code=EXIT_HALT)
    runner = jobs.GroupRunner()
    try:
        if ctx.jobs_adapter_factory:
            adapter = ctx.jobs_adapter_factory(runner)
        else:   # 실 CLI — MYCRM_JOBS_ALLOW_REAL_CLI=1 이 없으면 JobsError(약관 확인 전)
            cli_path = (cfg.get("summary") or {}).get("cli_path")
            adapter = jobs.ClaudeAdapter([cli_path] if cli_path else [], exec_runner=runner, paths=ctx.paths)
    except jobs.JobsError as e:
        return stop("adapter", e)

    def authenticate() -> str:
        try:
            info = _uploader(ctx, server, token).device()
        except DeviceError as e:
            if e.kind == "auth" or e.code in AUTH_CODES:
                return "auth"
            if e.kind == "network" or e.status == 429 or (e.status or 0) >= 500:
                return "network"
            return "error"
        return "ok" if isinstance(info, dict) and info.get("stream_id") == sid else "error"

    def make_worker(stopping, abort):
        client = (ctx.jobs_client_factory or jobs.Client)(server, token)
        return jobs.Worker(client, adapter, jobs.JsonState.for_paths(ctx.paths), sleep=lambda s: abort.wait(s),
                           stopping=stopping, abort=abort)

    try:
        res = jobs.serve_jobs(lock_path=ctx.paths.lock("jobs"), authenticate=authenticate, make_worker=make_worker,
                              runner=runner, log=log, **ctx.jobs_serve_kw)
    except jobs.JobsError as e:   # 깨진 jobs-state 등 — 고치지 않고 멈춘다
        return stop("state", e)
    s = res["stopped"]
    if s in ("signal", "worker-exit"):
        return EXIT_OK
    if s in ("auth", "auth-halted"):
        return EXIT_HALT
    return EXIT_ERR


def cmd_disconnect(ctx: Ctx, a) -> int:
    """서버 토큰 폐기(POST /api/imports/device/revoke)와 로컬 자격 증명 제거를 나눠 결과를 보인다.
    서버 폐기가 네트워크·서버 오류로 실패하면 로컬 토큰을 남기고 멈춘다(다시 시도 가능). --local-only 는 서버를 건너뛴다."""
    from .uploader import DeviceError
    cfg = _config.load(ctx.paths)
    sid = cfg.get("source_instance_id")
    if not sid:
        ctx.say("연결된 입력원이 없어요.")
        return EXIT_OK
    ks = ctx.keystore(cfg)
    try:
        token = ks.get(_config.token_account(sid))
    except keystore.KeyStoreError as e:
        ctx.say(f"자격 증명 저장소 접근 불가: {e}")
        return EXIT_ERR
    if a.local_only or not token or not cfg.get("server_url"):
        server_msg = "건너뜀 — 웹 계정 메뉴 › 연결 장치에서 이 장치를 폐기하세요."
    else:
        try:
            r = _uploader(ctx, cfg["server_url"], token).revoke()
            if r.get("revoked") is not True or r.get("stream_id") != sid:
                raise DeviceError("server", 200, "UNEXPECTED")
            server_msg = "폐기함"
        except DeviceError as e:
            if e.kind == "auth":
                server_msg = f"이미 무효({e.code or 'http-401'}) — 폐기·만료된 토큰"
            else:
                ctx.say("서버 토큰: 폐기 못 함(" + str(e) + "). 로컬 토큰은 그대로 두었어요.")
                ctx.say("다시 시도하거나, 웹에서 폐기한 뒤 `disconnect --local-only` 를 쓰세요.")
                return EXIT_ERR
    try:
        removed = ks.delete(_config.token_account(sid))
        key_removed = None
        if a.forget_hmac_key and cfg.get("hmac_namespace"):
            key_removed = ks.delete(_config.hmac_account(cfg["hmac_namespace"]))
    except keystore.KeyStoreError as e:
        ctx.say(f"서버 토큰: {server_msg}")
        ctx.say(f"자격 증명 저장소 접근 불가: {e}")
        return EXIT_ERR
    cfg.update({"source_instance_id": None, "token_expires_at": None})
    _config.save(ctx.paths, cfg)
    ctx.say(f"서버 토큰: {server_msg}")
    ctx.say("로컬 업로드 토큰: " + ("제거함" if removed else "없었음"))
    if key_removed is not None:
        ctx.say("HMAC 키: " + ("제거함(같은 사람 연결이 끊김)" if key_removed else "없었음"))
    else:
        ctx.say("HMAC 키: 유지(같은 사람 연결용)")
    ctx.say("예약·원문·대응표: 그대로(예약 해제는 `schedule remove`).")
    return EXIT_OK


COMMANDS = {"connect": cmd_connect, "validate": cmd_validate, "run-once": cmd_run_once, "status": cmd_status,
            "schedule": cmd_schedule, "serve": cmd_serve, "disconnect": cmd_disconnect}


def main(argv=None, **ctx_kw) -> int:
    p = _parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return EXIT_USAGE
    ctx = Ctx(a.home or _config.default_home(), **ctx_kw)
    try:
        return COMMANDS[a.cmd](ctx, a)
    except _config.ConfigError as e:
        ctx.say(f"설정 오류: {e}")
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())
