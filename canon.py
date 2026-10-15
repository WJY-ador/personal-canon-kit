#!/usr/bin/env python3
"""personal-canon-kit 명령줄. 데이터에 쓰는 일은 전부 이 명령으로 한다 — 대시보드는 읽기 전용이다.

macOS:   python3 canon.py <명령>
Windows: py canon.py <명령>
"""
import argparse
import hashlib
import json
import os
import sys
import webbrowser
from pathlib import Path

from canonkit import VERSION, checks, data as D, doctor, model, paths, schema, server, store
from canonkit.store import CanonError

try:                                    # Windows 콘솔에서 한글이 깨지지 않게
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def say(*lines):
    for l in lines:
        print(l)


def show_checks(rep):
    for r in rep["results"]:
        mark = {"ok": "✓", "warn": "!", "fail": "✗", "skip": "-"}[r["status"]]
        say(f" {mark} [{doctor.LABEL[r['status']]}] {r['code']:<12} {r['msg']}")
        if r["action"] and r["status"] in ("fail", "warn"):
            say(f"      → {r['action']}")
    say("", f"다음 조치: {rep['next']}")


# ── 명령 ───────────────────────────────────────────────

def c_init(a, data):
    target = Path(a.data).expanduser().resolve() if a.data else None
    if not target:
        raise CanonError("ARG-INVALID", "--data <폴더> 가 필요합니다.",
                         "예) python3 canon.py init --data ~/canon-data")
    if paths.PRODUCT_ROOT in [target, *target.parents]:
        raise CanonError("DATA-INSIDE-PRODUCT", "데이터 폴더를 제품 폴더 안에 둘 수 없습니다 — 업데이트 때 덮일 수 있습니다.",
                         "제품 폴더 밖의 경로를 고르세요.")
    created = D.init(target, "sample" if a.sample else "user")
    if not a.no_remember:
        store.write_json_atomic(paths.LOCAL_CONF, dict(paths.read_local_conf(), data_root=str(target)))
    say(f"데이터 폴더: {target}", f"새로 만든 파일: {', '.join(created) or '없음 (기존 파일은 그대로 둠)'}")
    if a.sample:
        say("※ 가짜 샘플입니다. 내 자료는 다른 폴더에 init 하세요.")
    say("", f"다음: {D.next_action(target)}")


def c_where(a, data):
    say(f"제품 폴더: {paths.PRODUCT_ROOT}", f"데이터 폴더: {data or '(정해지지 않음)'}", f"버전: {VERSION}")


def c_doctor(a, data):
    rep = doctor.run(data, a.port)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        say(f"점검 — personal-canon-kit {VERSION}", "")
        show_checks(rep)
    env_ok = not any(r["status"] == "fail" and r["code"] in ("ENV-PY", "ENV-OS") for r in rep["results"])
    if data and (Path(data) / "setup-state.json").exists() and env_ok:
        try:
            D.mark_step(data, "env")
        except CanonError:
            pass
    return 0 if rep["ok"] else 1


def c_diag(a, data):
    text = doctor.diag(data, a.port)
    if data and Path(data).exists():
        out = paths.sys_dir(data) / f"diag-{store.stamp()}.json"
        store.write_text_atomic(out, text)
        say(text, "", f"저장: {out}", "원문·판단 문장·경로·비밀값은 넣지 않았습니다. 보내기 전에 열어서 확인하세요.")
    else:
        say(text)


def c_status(a, data):
    D.require_data(data)
    p = D.load(data, "profile.json")
    st = D.load(data, "setup-state.json").get("steps", {})
    say("설정 단계")
    for k, label in D.SETUP_STEPS:
        say(f"  {'✓' if st.get(k) == 'done' else '·'} {k:<13} {label}")
    say("", "프로필 (확인/추정/미입력/꺼 둠)")
    for k, f in p["fields"].items():
        v = f.get("value")
        v = " | ".join(v) if isinstance(v, list) else (v or "")
        say(f"  [{schema.STATUS_KO[f['status']]}] {f.get('label', k)} ({k}): {v}")
    c = p["consent"]
    say("", f"동의 — 읽기 범위: {len(c['read_paths'])}곳, 모델 전송: {'허용' if c['model_send'] else '안 함'}"
            f"{'' if c.get('updated') else ' (아직 정하지 않음)'}")
    req = D.load(data, "tabs.json").get("requests", [])
    if req:
        say(f"보류한 요구 {len(req)}건: " + " / ".join(r["text"] for r in req))
    say("", f"다음: {D.next_action(data)}")


def c_setup(a, data):
    D.require_data(data)
    D.mark_step(data, a.step, "skipped" if a.skip else "done")
    say(f"단계 {a.step}: {'건너뜀' if a.skip else '완료'}", f"다음: {D.next_action(data)}")


def c_consent(a, data):
    D.require_data(data)
    ms = None if a.model_send is None else a.model_send == "yes"
    c = D.consent(data, a.read, ms)
    D.mark_step(data, "consent")
    say("동의 기록됨", *[f"  읽기 허용: {x}" for x in c["read_paths"]],
        f"  모델 전송: {'허용 — 정리(digest) 실행 때 그 기록 1편만 보냅니다' if c['model_send'] else '안 함 — digest --no-model 만 쓸 수 있습니다'}")


def c_profile(a, data):
    D.require_data(data)
    if a.action == "show":
        return c_status(a, data)
    if not a.key or a.value is None:
        raise CanonError("ARG-INVALID", "profile set <항목> <값> --status <상태>")
    f = D.profile_set(data, a.key, a.value, a.status, a.source or "")
    if a.key == "purpose" and a.status == "confirmed":
        D.mark_step(data, "profile")
    v = " | ".join(f["value"]) if isinstance(f["value"], list) else f["value"]
    say(f"{f.get('label', a.key)} = {v} [{schema.STATUS_KO[f['status']]}]")


def c_request(a, data):
    D.require_data(data)
    D.hold_request(data, a.text)
    say("미구현 요청으로 남겼습니다. 새 블록은 자동으로 만들지 않습니다.")


def c_question(a, data):
    D.require_data(data)
    if a.action == "add":
        qid = D.question_add(data, a.text)
        D.mark_step(data, "question")
        say(f"질문 {qid} 추가: {a.text}")
    elif a.action == "edit":
        D.question_edit(data, a.qid, a.text)
        say(f"질문 {a.qid} 수정됨")


def c_questions(a, data):
    D.require_data(data)
    qs = D.load(data, "questions.json")["questions"]
    say(*[f"{q['id']}  {q['text']}" for q in qs] or ["(질문 없음 — 일지부터 시작해도 됩니다)"])


def c_source(a, data):
    D.require_data(data)
    sid = D.source_add(data, a.label, a.path, a.model_send)
    D.mark_step(data, "source")
    say(f"자료 {sid} 등록: {a.label}")


def c_blocks(a, data):
    for bid, b in schema.BLOCKS.items():
        say(f"{bid:<11} {b['name']} — {b['desc']}  (읽는 것: {', '.join(b['reads']) or '없음'})")


def _print_preview(pv):
    say("탭 미리보기 (아직 적용 안 됨)", "")
    for t in pv["tabs"]:
        say(f"  [{t['change']}] {t['label']}  (내부 ID {t['id']})")
        for b in t["blocks"]:
            say(f"      {'켜짐' if b['on'] else '꺼짐'}  {b['name']} — {b['desc']}"
                + (f"  ← {b['why_off']}" if not b["on"] else "")
                + f"  · 읽는 것: {', '.join(b['reads']) or '없음'}")
    for o in pv["removed"]:
        say(f"  [없어짐] {o['label']} ({o['id']}) — 기록·후보는 지우지 않습니다")
    if pv["requests"]:
        say("", "  미구현 요청 (만들지 않음): " + " / ".join(r["text"] for r in pv["requests"]))
    say("", f"  바뀌는 파일: {', '.join(pv['writes'])}", f"  모델 전송: {pv['model_send']}",
        f"  되돌리기: {pv['rollback']}", "",
        "적용: python3 canon.py tabs apply   ·   취소: python3 canon.py tabs cancel")


def c_tabs(a, data):
    D.require_data(data)
    if a.action == "propose":
        specs = []
        for spec in a.tab or []:
            if "=" not in spec:
                raise CanonError("ARG-INVALID", f"--tab 형식은 \"이름=블록,블록\" 입니다: {spec}")
            label, blocks = spec.split("=", 1)
            specs.append((label.strip(), [b.strip() for b in blocks.split(",") if b.strip()]))
        if not specs:
            raise CanonError("ARG-INVALID", "--tab \"이름=블록,블록\" 을 하나 이상 주세요.", "블록 목록: python3 canon.py blocks")
        _print_preview(D.propose_tabs(data, specs))
    elif a.action == "rename":
        _print_preview(D.rename_tab(data, a.args[0], a.args[1]))
    elif a.action == "preview":
        _print_preview(D.preview(data))
    elif a.action == "apply":
        bid = D.apply_tabs(data)
        D.mark_step(data, "tabs")
        say(f"적용됨. 적용 전 상태는 백업 {bid} 에 있습니다.", "확인: python3 canon.py serve")
    elif a.action == "cancel":
        say("제안을 버렸습니다. 기존 탭은 그대로입니다." if D.cancel_tabs(data) else "대기 중인 제안이 없습니다.")
    elif a.action == "show":
        for t in D.load(data, "tabs.json")["tabs"]:
            say(f"{t['id']}  {t['label']}: {', '.join(t['blocks'])}")


def c_record(a, data):
    D.require_data(data)
    if not a.text and not a.file:
        raise CanonError("ARG-INVALID", "--text 또는 --file 이 필요합니다.")
    jid = D.record_add(data, a.title, a.text, a.question, from_file=a.file)
    D.mark_step(data, "first_record")
    say(f"기록 {jid} 저장 (원문 그대로): {Path(data) / 'journal' / (jid + '.md')}",
        "끝의 '이게 어디에 붙나' 네 줄을 채우면 --no-model 정리에서 내 메모 후보가 됩니다.")


def c_journal(a, data):
    D.require_data(data)
    recs = D.list_journal(data)
    say(*[f"{r['id']}  {r['title']}  ({r['meta'].get('created', '')[:16]}"
          f"{' · ' + r['meta']['question'] if r['meta'].get('question') else ''})" for r in recs]
        or ["(기록 없음 — record add 로 1편부터)"])


def c_digest(a, data):
    D.require_data(data)
    rec = D.get_journal(data, a.record)
    qs = {q["id"]: q for q in D.load(data, "questions.json")["questions"]}
    if a.question not in qs:
        raise CanonError("ARG-INVALID", f"질문 {a.question} 이(가) 없습니다.",
                         "질문이 아직 없으면 question add 로 하나 만들거나, 일지만 쌓아 두세요.")
    runs = store.read_runs(data).get("digest", {})
    store.set_run(data, "digest", state="running", started=store.now(), record=rec["id"],
                  question=a.question, mode="no-model" if a.no_model else "model",
                  error=None, error_code=None, action=None,
                  last_success=runs.get("last_success"))
    try:
        if a.no_model:
            items = [{"text": j, "quote": j} for j in D.self_judgments(rec)]
            origin = "self"
            if not items:
                say("기록 끝 '- 판단:' 줄이 비어 있어 후보가 없습니다. 채운 뒤 다시 실행하세요.")
        else:
            prof = D.load(data, "profile.json")
            if not prof["consent"].get("model_send"):
                raise CanonError("CONSENT-MISSING", "모델 전송에 동의하지 않았습니다.",
                                 "동의하려면 consent --model-send yes, 아니면 --no-model 로 실행하세요.")
            say(f"모델로 보내는 것: 기록 {rec['id']} 본문 {len(rec['body'])}자 + 질문 {a.question} 문장. 그 밖의 파일은 보내지 않습니다.")
            ans = model.ask(model.DIGEST_PROMPT.format(question=qs[a.question]["text"], body=rec["body"]),
                            cwd=str(paths.sys_dir(data)))
            items = model.extract_json(ans).get("candidates", [])
            if not isinstance(items, list):
                raise CanonError("MODEL-FORMAT", "모델 답의 candidates 가 목록이 아닙니다.", "한 번만 다시 실행하세요.")
            origin = "ai"
        added, dropped = D.add_candidates(data, rec, a.question, items, origin)
        if items and not added and all(r != "이미 있는 후보" for _, r in dropped):
            raise CanonError("MODEL-GROUNDING", "후보가 모두 원문 근거 검사에서 떨어졌습니다.",
                             "이전 정상본은 그대로입니다. 한 번만 다시 실행하고, 반복되면 issue 로 남기세요.")
        sh = D.build_sheet(data, a.question)
    except CanonError as e:
        store.set_run(data, "digest", state="failed", finished=store.now(), error=e.message,
                      error_code=e.code, action=e.action)
        raise
    store.set_run(data, "digest", state="ok", finished=store.now(), last_success=store.now())
    D.mark_step(data, "first_sheet")
    say(f"후보 {len(added)}건 추가 (확인 대기): {', '.join(added) or '없음'}")
    for it, why in dropped:
        say(f"  버림: {why} — {str(it.get('text', ''))[:60]}")
    say(f"한 장 갱신: {sh['path']} (확정 {sh['confirmed']} · 대기 {sh['pending']})",
        "후보는 아직 내 판단이 아닙니다. 원문과 대조해 candidate confirm / exclude 하세요.")


def c_candidates(a, data):
    D.require_data(data)
    for c in D.load(data, "candidates.json")["candidates"]:
        if a.question and c["question"] != a.question:
            continue
        if not a.all and c["status"] == "excluded":
            continue
        tag = {"pending": "대기", "confirmed": "확정", "excluded": "제외"}[c["status"]]
        who = "AI 제안" if c["origin"] == "ai" else "내 메모"
        say(f"{c['id']} [{tag}·{who}] {c['text']}", f"      근거 {c['evidence']['record']}: “{c['evidence']['quote']}”")


def c_candidate(a, data):
    D.require_data(data)
    if a.action == "edit":
        c = D.candidate_set(data, a.cid, text=a.text)
    else:
        c = D.candidate_set(data, a.cid, status={"confirm": "confirmed", "exclude": "excluded",
                                                  "pending": "pending"}[a.action])
    D.build_sheet(data, c["question"])
    say(f"{c['id']} → {c['status']}  (한 장 {c['question']} 갱신됨)")


def c_sheet(a, data):
    D.require_data(data)
    qids = [q["id"] for q in D.load(data, "questions.json")["questions"]] if a.qid == "all" else [a.qid]
    for q in qids:
        r = D.build_sheet(data, q)
        say(f"{q}: {r['path']} (확정 {r['confirmed']} · 대기 {r['pending']})"
            + (f" ⚠ 원문 없음 {r['missing']}" if r["missing"] else ""))


def c_serve(a, data):
    httpd = server.serve(data, a.port)
    url = f"http://127.0.0.1:{a.port}/"
    say(f"대시보드: {url}  (읽기 전용 · 끄려면 Ctrl+C)")
    if a.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        say("종료")
    finally:
        httpd.server_close()


def c_backup(a, data):
    D.require_data(data)
    say(f"백업 {store.backup(data, a.label)} 생성")


def c_backups(a, data):
    D.require_data(data)
    bks = store.list_backups(data)
    say(*[f"{b['id']}  ({len(b['files'])}개 파일){' 손상' if b.get('broken') else ''}" for b in bks]
        or ["(백업 없음)"])


def c_restore(a, data):
    D.require_data(data)
    r = store.restore(data, a.bid)
    say(f"복구 완료: 파일 {len(r['restored'])}개", f"복구 직전 상태 백업: {r['pre_restore_backup']}")
    if r["kept_newer"]:
        say("백업 이후 새로 생긴 파일은 지우지 않고 남겼습니다: " + ", ".join(r["kept_newer"]))
    say("확인: python3 canon.py doctor")


def _tabs_hash(data):
    return hashlib.sha256((Path(data) / "tabs.json").read_bytes()).hexdigest()


def c_drill(a, data):
    D.require_data(data)
    f = paths.sys_dir(data) / "drill.json"
    if a.action == "start":
        D.load(data, "tabs.json")                  # 정상 상태에서만 시작
        h = _tabs_hash(data)
        bid = store.backup(data, "drill")
        store.write_json_atomic(f, {"backup": bid, "tabs_sha256": h, "started": store.now()})
        (Path(data) / "tabs.json").write_text("{ 리허설로 일부러 깨뜨린 설정", encoding="utf-8")
        say("복구 리허설 시작 — tabs.json 을 일부러 깨뜨렸습니다.", "",
            "1) python3 canon.py doctor          → 실패 항목과 다음 조치를 읽으세요",
            "2) python3 canon.py serve           → 화면이 깨진 상태를 어떻게 알리는지 보세요 (선택)",
            f"3) python3 canon.py restore {bid}",
            "4) python3 canon.py drill finish    → 같은 탭이 돌아왔는지 검사합니다")
    else:
        if not f.exists():
            raise CanonError("DRILL-NONE", "진행 중인 리허설이 없습니다.", "python3 canon.py drill start")
        st = json.loads(f.read_text(encoding="utf-8"))
        fails = [r for r in doctor.data_checks(data) if r["status"] == "fail"]
        if fails:
            raise CanonError("DRILL-NOT-RECOVERED", f"아직 실패 {len(fails)}건: {fails[0]['msg']}",
                             f"python3 canon.py restore {st['backup']}")
        if _tabs_hash(data) != st["tabs_sha256"]:
            raise CanonError("DRILL-MISMATCH", "탭 설정이 리허설 전과 다릅니다.",
                             f"python3 canon.py restore {st['backup']}")
        f.unlink()
        D.mark_step(data, "drill")
        say("복구 리허설 통과 — 깨짐 감지 → 백업 복구 → 같은 탭 재조회까지 확인했습니다.")


def c_issue(a, data):
    D.require_data(data)
    il = Path(data) / "issue-log.md"
    with store.lock(data, "issue add"):
        text = il.read_text(encoding="utf-8") if il.exists() else ""
        text = text.rstrip() + f"\n\n## {store.now()[:16]} · {a.kind}\n\n- 무엇이: {a.text}\n- 다음 조치:\n- 같은 원인 두 번째면: 규칙으로 올릴 것\n"
        store.write_text_atomic(il, text)
    say(f"issue-log.md 에 남겼습니다 ({a.kind}).")


def c_next(a, data):
    D.require_data(data)
    say(D.next_action(data))


def c_check(a, data):
    if a.update_manifest:
        say(f"manifest 파일 목록 갱신: {checks.update_manifest()}개")
    extra = paths.read_local_conf().get("scrub_extra", []) + \
        [x.strip() for x in os.environ.get("CANON_SCRUB_EXTRA", "").split(",") if x.strip()]
    issues = checks.run(set(COMMANDS), extra)
    for lvl, where, msg in issues:
        say(f" {'✗' if lvl == 'fail' else '!'} {where}  {msg}")
    fails = [i for i in issues if i[0] == "fail"]
    say("", f"패키지 검사: 실패 {len(fails)} · 경고 {len(issues) - len(fails)}")
    return 1 if fails else 0


COMMANDS = {}


def build_parser():
    ap = argparse.ArgumentParser(prog="canon.py", description="personal-canon-kit — 내 기록을 내 질문·화면으로")
    ap.add_argument("--data", help="데이터 폴더 (기본: CANON_DATA 또는 local.json)")
    ap.add_argument("--version", action="version", version=VERSION)
    sp = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_):
        p = sp.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        COMMANDS[name] = fn
        return p

    p = add("init", c_init, "데이터 폴더 만들기 (있는 파일은 안 건드림)")
    p.add_argument("--sample", action="store_true", help="가짜 샘플로 만들기")
    p.add_argument("--no-remember", action="store_true", help="local.json 에 기억하지 않기")
    add("where", c_where, "제품·데이터 폴더 위치")
    for name, fn, h in (("doctor", c_doctor, "환경·데이터 점검"), ("diag", c_diag, "지원용 마스킹 진단 만들기")):
        p = add(name, fn, h)
        p.add_argument("--port", type=int, default=8765)
        if name == "doctor":
            p.add_argument("--json", action="store_true")
    add("status", c_status, "설정 단계·프로필·동의 보기")
    p = add("setup", c_setup, "설정 단계 완료 표시")
    p.add_argument("action", choices=["done"])
    p.add_argument("step")
    p.add_argument("--skip", action="store_true")
    p = add("consent", c_consent, "읽기 범위·모델 전송 동의")
    p.add_argument("--read", nargs="*", help="읽어도 되는 폴더·파일 (여러 개)")
    p.add_argument("--model-send", choices=["yes", "no"])
    p = add("profile", c_profile, "프로필 항목 쓰기·보기")
    p.add_argument("action", choices=["set", "show"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--status", choices=schema.STATUSES, default="confirmed")
    p.add_argument("--source", help="어디서 알았나 (예: 사용자 답, 글 추정)")
    p = add("request", c_request, "카탈로그 밖 요구를 미구현 요청으로 남기기")
    p.add_argument("text")
    p = add("question", c_question, "질문 추가·수정")
    p.add_argument("action", choices=["add", "edit"])
    p.add_argument("rest", nargs="+")
    add("questions", c_questions, "질문 목록")
    p = add("source", c_source, "자료 위치 등록")
    p.add_argument("action", choices=["add"])
    p.add_argument("path")
    p.add_argument("--label", required=True)
    p.add_argument("--model-send", action="store_true")
    add("blocks", c_blocks, "블록 카탈로그")
    p = add("tabs", c_tabs, "탭 제안·미리보기·적용·취소")
    p.add_argument("action", choices=["propose", "preview", "apply", "cancel", "rename", "show"])
    p.add_argument("args", nargs="*")
    p.add_argument("--tab", action="append", help="\"표시 이름=블록,블록\"")
    p = add("record", c_record, "기록 1편 추가 (원문 보존)")
    p.add_argument("action", choices=["add"])
    p.add_argument("--title", required=True)
    p.add_argument("--text")
    p.add_argument("--file")
    p.add_argument("--question")
    add("journal", c_journal, "기록 목록")
    p = add("digest", c_digest, "기록 1편 → 판단 후보 → 한 장 (명시 실행)")
    p.add_argument("--record", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--no-model", action="store_true", help="모델 없이 기록 끝 '- 판단:' 줄만 후보로")
    p = add("candidates", c_candidates, "판단 후보 목록")
    p.add_argument("--question")
    p.add_argument("--all", action="store_true", help="제외한 것도 보기")
    p = add("candidate", c_candidate, "후보 확인·제외·수정")
    p.add_argument("action", choices=["confirm", "exclude", "pending", "edit"])
    p.add_argument("cid")
    p.add_argument("text", nargs="?")
    p = add("sheet", c_sheet, "질문별 한 장 다시 만들기 (모델 안 씀)")
    p.add_argument("action", choices=["build"])
    p.add_argument("qid", help="질문 ID 또는 all")
    p = add("serve", c_serve, "대시보드 열기 (읽기 전용)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="브라우저 열기")
    p = add("backup", c_backup, "지금 상태 백업")
    p.add_argument("--label", default="manual")
    add("backups", c_backups, "백업 목록")
    p = add("restore", c_restore, "백업 되돌리기 (지금 상태도 먼저 백업)")
    p.add_argument("bid")
    p = add("drill", c_drill, "복구 리허설")
    p.add_argument("action", choices=["start", "finish"])
    p = add("issue", c_issue, "issue-log 에 사건 남기기")
    p.add_argument("action", choices=["add"])
    p.add_argument("text")
    p.add_argument("--kind", default="사용자 조치", choices=["사용자 조치", "환경 미지원", "재설치·롤백", "제품 결함 후보"])
    add("next", c_next, "다음 행동 하나")
    p = add("check", c_check, "패키지 검사 (제작자용)")
    p.add_argument("--update-manifest", action="store_true")
    return ap


def _pull_data_arg(argv):
    """--data 를 명령 앞뒤 어디에 써도 되게 미리 뽑는다."""
    rest, data = [], None
    it = iter(argv)
    for x in it:
        if x == "--data":
            data = next(it, None)
        elif x.startswith("--data="):
            data = x.split("=", 1)[1]
        else:
            rest.append(x)
    return rest, data


def main(argv=None):
    ap = build_parser()
    argv, data_arg = _pull_data_arg(sys.argv[1:] if argv is None else list(argv))
    a = ap.parse_args(argv)
    a.data = data_arg
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    if a.cmd == "question":
        if a.action == "add":
            a.text = " ".join(a.rest)
        else:
            if len(a.rest) < 2:
                ap.error("question edit <질문ID> <새 문장>")
            a.qid, a.text = a.rest[0], " ".join(a.rest[1:])
    if a.cmd == "tabs" and a.action == "rename" and len(a.args) != 2:
        ap.error("tabs rename <탭ID> <새 이름>")
    if a.cmd == "candidate" and a.action == "edit" and not a.text:
        ap.error("candidate edit <후보ID> <새 문장>")
    data = paths.resolve_data_root(a.data) if a.cmd != "init" else None
    try:
        rc = a.fn(a, data)
        return rc or 0
    except CanonError as e:
        print(f"\n[오류 {e.code}] {e.message}", file=sys.stderr)
        if e.action:
            print(f"다음 조치: {e.action}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
