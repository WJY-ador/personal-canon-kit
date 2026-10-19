"""데이터 폴더 읽기·쓰기. 쓰기 함수는 전부 잠금 안에서 원자적으로 쓴다.

데이터 흐름: 선택 원문 → 일지(journal) → 판단 후보(candidates, pending) → 사용자 확인 → 질문별 한 장(sheets).
한 장은 원문·후보를 대체하지 않는다. 후보는 사용자가 confirm 해야만 "내 판단"이 된다.
"""
import json
import re
import shutil
from pathlib import Path

from . import SCHEMA_VERSION, paths, schema
from .store import CrmError, backup, lock, now, read_json, write_json_atomic, write_text_atomic

SETUP_STEPS = [
    ("env", "환경 점검"),
    ("consent", "읽기 범위·모델 전송 동의"),
    ("profile", "목적 확인"),
    ("question", "질문 1개 또는 일지부터"),
    ("source", "첫 자료 1종"),
    ("tabs", "내 탭 승인"),
    ("first_record", "기록 1편"),
    ("first_sheet", "첫 한 장"),
    ("reopen", "새 세션에서 다시 열기"),
    ("drill", "복구 리허설"),
]


# ── 공통 ───────────────────────────────────────────────

def require_data(data):
    if data is None:
        raise CrmError("DATA-UNSET", "데이터 폴더가 정해지지 않았습니다.",
                         "python3 crm.py init --data <폴더> 로 먼저 만드세요.")
    data = Path(data)
    if not (data / "meta.json").exists():
        raise CrmError("DATA-MISSING", f"데이터 폴더가 아닙니다: {paths.mask_path(data)}",
                         "경로를 확인하거나 python3 crm.py init --data <폴더> 로 만드세요.")
    return data


def load(data, name):
    d = read_json(Path(data) / name)
    errs = schema.VALIDATORS[name](d)
    if errs:
        raise CrmError("SCHEMA-INVALID", f"{name}: " + "; ".join(errs),
                         "python3 crm.py doctor 로 확인한 뒤 restore 로 되돌리세요.")
    return d


def save(data, name, d):
    write_json_atomic(Path(data) / name, d, schema.VALIDATORS[name])


def next_id(prefix, existing, width):
    nums = [int(x.split("-")[-1]) for x in existing if x.startswith(prefix)]
    return f"{prefix}{(max(nums) + 1 if nums else 1):0{width}d}"


# ── init ───────────────────────────────────────────────

def init(data, kind="user"):
    """템플릿으로 데이터 폴더를 만든다. 이미 있는 파일은 건드리지 않는다."""
    data = Path(data)
    created = []
    if kind == "sample":
        if data.exists() and any(data.iterdir()):
            raise CrmError("DATA-EXISTS", "샘플은 빈 폴더에만 풉니다.", "다른 빈 폴더를 지정하세요.")
        shutil.copytree(paths.SAMPLE_DATA, data, dirs_exist_ok=True)
        return ["(샘플 전체)"]
    data.mkdir(parents=True, exist_ok=True)
    for d in paths.USER_DIRS:
        (data / d).mkdir(exist_ok=True)
    for name in paths.SETTINGS:
        t = data / name
        if not t.exists():
            d = json.loads((paths.TEMPLATES / name).read_text(encoding="utf-8"))
            if name == "meta.json":
                d["kind"] = kind
                d["created"] = now()
            write_json_atomic(t, d, schema.VALIDATORS[name])
            created.append(name)
    il = data / "issue-log.md"
    if not il.exists():
        shutil.copy2(paths.TEMPLATES / "issue-log.md", il)
        created.append("issue-log.md")
    return created


# ── setup 진행 상태 ─────────────────────────────────────

def mark_step(data, step, value="done"):
    keys = [k for k, _ in SETUP_STEPS]
    if step not in keys:
        raise CrmError("ARG-INVALID", f"알 수 없는 단계: {step}", "가능한 값: " + ", ".join(keys))
    with lock(data, f"setup {step}"):
        st = load(data, "setup-state.json")
        st.setdefault("steps", {})[step] = value
        st["last_step"] = step
        st["updated"] = now()
        save(data, "setup-state.json", st)


# ── 프로필·동의 ─────────────────────────────────────────

def profile_set(data, key, value, status, source=""):
    if status not in schema.STATUSES:
        raise CrmError("ARG-INVALID", f"상태는 {', '.join(schema.STATUSES)} 중 하나입니다.")
    with lock(data, "profile set"):
        p = load(data, "profile.json")
        f = p["fields"].get(key)
        if f is None:
            raise CrmError("ARG-INVALID", f"프로필에 없는 항목: {key}",
                             "가능한 항목: " + ", ".join(p["fields"]))
        if key == "daily_views":
            value = [v.strip() for v in value.split("|") if v.strip()] if isinstance(value, str) else value
        f.update({"value": value, "status": status, "source": source, "updated": now()})
        save(data, "profile.json", p)
    return f


def consent(data, read_paths=None, model_send=None):
    with lock(data, "consent"):
        p = load(data, "profile.json")
        c = p["consent"]
        if read_paths is not None:
            c["read_paths"] = [str(Path(x).expanduser().resolve()) for x in read_paths]
        if model_send is not None:
            c["model_send"] = bool(model_send)
        c["updated"] = now()
        save(data, "profile.json", p)
    return c


def hold_request(data, text):
    with lock(data, "request"):
        t = load(data, "tabs.json")
        t.setdefault("requests", []).append({"text": text, "created": now(), "status": "미구현 요청"})
        save(data, "tabs.json", t)


# ── 질문·자료 ───────────────────────────────────────────

def question_add(data, text):
    with lock(data, "question add"):
        q = load(data, "questions.json")
        qid = next_id("q-", [x["id"] for x in q["questions"]], 3)
        q["questions"].append({"id": qid, "text": text.strip(), "status": "active", "created": now()})
        save(data, "questions.json", q)
    return qid


def question_edit(data, qid, text):
    with lock(data, "question edit"):
        q = load(data, "questions.json")
        for x in q["questions"]:
            if x["id"] == qid:
                x["text"], x["updated"] = text.strip(), now()
                save(data, "questions.json", q)
                return
    raise CrmError("ARG-INVALID", f"질문 {qid} 이(가) 없습니다.")


def source_add(data, label, path, model_send=False):
    p = Path(path).expanduser().resolve()
    allowed = load(data, "profile.json")["consent"]["read_paths"]
    if not any(_inside(p, Path(a)) for a in allowed):
        raise CrmError("CONSENT-MISSING", "읽기 동의한 범위 밖의 자료입니다.",
                         f"먼저 python3 crm.py consent --read \"{p}\" 로 동의를 남기세요.")
    if not p.exists():
        raise CrmError("SOURCE-MISSING", f"자료 위치가 없습니다: {paths.mask_path(p)}", "경로를 확인하세요.")
    with lock(data, "source add"):
        s = load(data, "sources.json")
        sid = next_id("s-", [x["id"] for x in s["sources"]], 3)
        s["sources"].append({"id": sid, "label": label, "path": str(p),
                             "kind": "folder" if p.is_dir() else "file",
                             "model_send": bool(model_send), "added": now()})
        save(data, "sources.json", s)
    return sid


def _inside(p, root):
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


# ── 일지(기록) ──────────────────────────────────────────

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def parse_journal(path):
    text = Path(path).read_text(encoding="utf-8")
    meta, body = {}, text
    m = FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    title = next((l[2:].strip() for l in body.splitlines() if l.startswith("# ")), Path(path).stem)
    return {"id": meta.get("id", Path(path).stem), "meta": meta, "title": title,
            "body": body, "path": Path(path)}


def list_journal(data):
    out = []
    for p in sorted((Path(data) / "journal").glob("j-*.md"), reverse=True):
        try:
            out.append(parse_journal(p))
        except (OSError, UnicodeDecodeError):
            out.append({"id": p.stem, "meta": {}, "title": "(읽기 실패)", "body": "", "path": p, "broken": True})
    return out


def get_journal(data, jid):
    if not schema.ID_RE["journal"].match(jid):
        raise CrmError("ARG-INVALID", f"기록 ID 형식 오류: {jid}")
    p = Path(data) / "journal" / f"{jid}.md"
    if not p.exists():
        raise CrmError("RECORD-MISSING", f"기록 {jid} 이(가) 없습니다.",
                         "파일이 옮겨졌는지 journal 폴더를 확인하세요.")
    return parse_journal(p)


def record_add(data, title, text, question=None, source="직접 입력", from_file=None):
    """기록 1편을 원문 그대로 일지에 남긴다. 파일에서 가져오면 읽기 동의 범위를 먼저 본다."""
    if from_file:
        p = Path(from_file).expanduser().resolve()
        allowed = load(data, "profile.json")["consent"]["read_paths"]
        if not any(_inside(p, Path(a)) for a in allowed):
            raise CrmError("CONSENT-MISSING", "읽기 동의한 범위 밖의 파일입니다.",
                             f"python3 crm.py consent --read \"{p.parent}\" 로 동의한 뒤 다시 실행하세요.")
        text = p.read_text(encoding="utf-8")
        source = f"파일:{p.name}"
    if not (text or "").strip():
        raise CrmError("ARG-INVALID", "기록 내용이 비었습니다.")
    if question:
        qs = [x["id"] for x in load(data, "questions.json")["questions"]]
        if question not in qs:
            raise CrmError("ARG-INVALID", f"질문 {question} 이(가) 없습니다.", "python3 crm.py questions 로 확인하세요.")
    tail = (paths.TEMPLATES / "journal-tail.md").read_text(encoding="utf-8")
    with lock(data, "record add"):
        day = now()[:10].replace("-", "")
        existing = [p.stem for p in (Path(data) / "journal").glob(f"j-{day}-*.md")]
        jid = next_id(f"j-{day}-", existing, 2)
        body = text.rstrip() + "\n"
        if "## 이게 어디에 붙나" not in body:
            body += "\n" + tail
        doc = (f"---\nid: {jid}\ncreated: {now()}\nsource: {source}\n"
               f"question: {question or ''}\norigin: self\n---\n# {title.strip()}\n\n{body}")
        write_text_atomic(Path(data) / "journal" / f"{jid}.md", doc)
    return jid


def self_judgments(rec):
    """기록 끝 '이게 어디에 붙나' 절의 '- 판단:' 줄 — 사용자가 직접 쓴 판단 메모."""
    out, inside = [], False
    for line in rec["body"].splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## 이게 어디에 붙나"
            continue
        if inside:
            m = re.match(r"^\s*-\s*판단\s*:\s*(.+)$", line)
            if m and m.group(1).strip():
                out.append(m.group(1).strip())
    return out


# ── 판단 후보 ───────────────────────────────────────────

def add_candidates(data, rec, question, items, origin):
    """items: [{"text", "quote"}]. quote 가 원문에 그대로 없으면 버린다.
    같은 기록·같은 근거 구간의 후보는 다시 넣지 않는다 — 사용자가 문장을 고친 뒤 재실행해도 원래 문장이 되살아나지 않게.
    (추가된 ID 목록, 버린 항목과 이유) 를 돌려준다."""
    added, dropped = [], []
    with lock(data, "candidates add"):
        cd = load(data, "candidates.json")
        have = {(c["evidence"]["record"], c["evidence"]["quote"]) for c in cd["candidates"]}
        for it in items:
            text, quote = (it.get("text") or "").strip(), (it.get("quote") or "").strip()
            if not text or not quote:
                dropped.append((it, "판단 문장이나 근거 인용이 비었음"))
                continue
            if quote not in rec["body"]:
                dropped.append((it, "근거 인용이 원문에 그대로 없음"))
                continue
            if (rec["id"], quote) in have:
                dropped.append((it, "이미 있는 후보"))
                continue
            cid = next_id("c-", [c["id"] for c in cd["candidates"]], 4)
            cd["candidates"].append({
                "id": cid, "question": question, "text": text, "origin": origin, "status": "pending",
                "evidence": {"record": rec["id"], "quote": quote}, "created": now(), "updated": now()})
            have.add((rec["id"], quote))
            added.append(cid)
        if added:
            save(data, "candidates.json", cd)
    return added, dropped


def candidate_set(data, cid, status=None, text=None):
    with lock(data, "candidate set"):
        cd = load(data, "candidates.json")
        for c in cd["candidates"]:
            if c["id"] == cid:
                if status:
                    c["status"] = status
                if text:
                    c["text"] = text.strip()
                    c["edited_by_user"] = True
                c["updated"] = now()
                save(data, "candidates.json", cd)
                return c
    raise CrmError("ARG-INVALID", f"후보 {cid} 이(가) 없습니다.", "python3 crm.py candidates 로 확인하세요.")


# ── 질문별 한 장 ────────────────────────────────────────

def build_sheet(data, qid):
    """확정·대기 후보로 한 장을 다시 만든다. 모델을 부르지 않는다(결정적).
    임시 파일 → 링크·ID 검사 → 교체. 이전 판은 .crm/history 에 남긴다."""
    qs = {x["id"]: x for x in load(data, "questions.json")["questions"]}
    if qid not in qs:
        raise CrmError("ARG-INVALID", f"질문 {qid} 이(가) 없습니다.")
    cands = [c for c in load(data, "candidates.json")["candidates"] if c["question"] == qid]
    conf = [c for c in cands if c["status"] == "confirmed"]
    pend = [c for c in cands if c["status"] == "pending"]
    recs = {}
    for c in cands:
        rid = c["evidence"]["record"]
        if rid not in recs:
            try:
                recs[rid] = get_journal(data, rid)
            except CrmError:
                recs[rid] = None
    missing = [r for r, v in recs.items() if v is None]
    built = now()

    def cite(c):
        rid = c["evidence"]["record"]
        return f"근거: [{rid}](../journal/{rid}.md) — “{c['evidence']['quote']}”"

    L = [f"# {qs[qid]['text']}", "",
         f"> 질문 {qid} · 갱신 {built} · 자동 생성 파일 — 직접 고치지 말고 후보를 확인·제외하세요.", ""]
    L += ["## 내가 확인한 판단", ""]
    L += [f"- {c['text']}  \n  {cite(c)}" for c in conf] or ["(아직 없음 — 아래 후보를 확인하면 여기로 옵니다)"]
    L += ["", "## 확인 대기 — 아직 내 판단 아님", ""]
    L += [f"- [{'AI 제안' if c['origin'] == 'ai' else '내 메모'}] {c['text']} ({c['id']})  \n  {cite(c)}"
          for c in pend] or ["(없음)"]
    L += ["", "## 연결된 기록", ""]
    L += [f"- [{rid}](../journal/{rid}.md) {r['title'] if r else '(원문 없음 — 위치를 확인하세요)'}"
          for rid, r in sorted(recs.items())] or ["(없음)"]
    if missing:
        L += ["", f"⚠ 원문을 찾지 못한 기록: {', '.join(missing)} — 이 부분은 미완료입니다."]
    nxt = (f"python3 crm.py candidate confirm {pend[0]['id']} (또는 exclude)" if pend
           else "python3 crm.py record add 로 기록 1편 추가")
    L += ["", "## 다음 행동", "", f"- {nxt}", ""]
    text = "\n".join(L)

    def check(s):
        for c in conf:
            if c["text"] not in s:
                raise CrmError("SHEET-CHECK", f"검사 실패: 확정 판단 {c['id']} 이 한 장에 없음",
                                 "이전 한 장은 그대로입니다. issue 로 남겨 주세요.")
    out = Path(data) / "sheets" / f"{qid}.md"
    with lock(data, "sheet build"):
        if out.exists():
            hist = paths.sys_dir(data) / "history" / "sheets"
            hist.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out, hist / f"{qid}-{built[:19].replace(':', '')}.md")
        write_text_atomic(out, text, check)
    return {"path": out, "confirmed": len(conf), "pending": len(pend), "missing": missing, "built": built}


def sheet_info(data, qid):
    p = Path(data) / "sheets" / f"{qid}.md"
    if not p.exists():
        return {"exists": False}
    text = p.read_text(encoding="utf-8")
    m = re.search(r"갱신 (\S+)", text)
    built = m.group(1) if m else ""
    cands = [c for c in load(data, "candidates.json")["candidates"] if c["question"] == qid]
    stale = any(c.get("updated", "") > built for c in cands)
    return {"exists": True, "text": text, "built": built, "stale": stale}


# ── 탭 미리보기·승인 ────────────────────────────────────

def pending_tabs_path(data):
    return paths.sys_dir(data) / "pending-tabs.json"


def propose_tabs(data, specs):
    """specs: [(표시 이름, [블록 ID...])]. 적용하지 않고 제안만 저장한 뒤 미리보기를 돌려준다.
    표시 이름이 같은 기존 탭은 내부 ID 를 이어받는다 — 이름을 바꿔도 연결이 끊기지 않게 하려면 --rename 을 쓴다."""
    cur = load(data, "tabs.json")
    by_label = {t["label"]: t["id"] for t in cur["tabs"]}
    used = [t["id"] for t in cur["tabs"]]
    tabs, unknown = [], []
    for label, blocks in specs:
        bad = [b for b in blocks if b not in schema.BLOCKS]
        unknown += bad
        tid = by_label.get(label)
        if not tid:
            tid = next_id("t-", used, 3)
            used.append(tid)
        tabs.append({"id": tid, "label": label, "blocks": [b for b in blocks if b in schema.BLOCKS]})
    if unknown:
        raise CrmError("BLOCK-UNKNOWN", f"카탈로그에 없는 블록: {', '.join(unknown)}",
                         "python3 crm.py blocks 로 목록을 보고, 없는 것은 request 로 남기세요.")
    new = dict(cur, tabs=tabs, revision=cur.get("revision", 0) + 1)
    errs = schema.tabs(new)
    if errs:
        raise CrmError("SCHEMA-INVALID", "; ".join(errs))
    write_json_atomic(pending_tabs_path(data), {"proposed": now(), "tabs": new})
    return preview(data)


def rename_tab(data, tid, label):
    """표시 이름만 바꾼다. 내부 ID 는 그대로라 딥링크(#t-001)와 연결이 유지된다."""
    cur = load(data, "tabs.json")
    if tid not in {t["id"] for t in cur["tabs"]}:
        raise CrmError("ARG-INVALID", f"탭 {tid} 이(가) 없습니다.")
    new = json.loads(json.dumps(cur))
    for t in new["tabs"]:
        if t["id"] == tid:
            t["label"] = label.strip()
    new["revision"] = cur.get("revision", 0) + 1
    errs = schema.tabs(new)
    if errs:
        raise CrmError("SCHEMA-INVALID", "; ".join(errs))
    write_json_atomic(pending_tabs_path(data), {"proposed": now(), "tabs": new})
    return preview(data)


def preview(data):
    p = pending_tabs_path(data)
    if not p.exists():
        raise CrmError("PREVIEW-NONE", "적용 대기 중인 탭 제안이 없습니다.", "python3 crm.py tabs propose 로 먼저 제안하세요.")
    new = json.loads(p.read_text(encoding="utf-8"))["tabs"]
    cur = load(data, "tabs.json")
    have = availability(data)
    rows = []
    for t in new["tabs"]:
        blocks = []
        for b in t["blocks"]:
            meta = schema.BLOCKS[b]
            on = meta["needs"] is None or have.get(meta["needs"], False)
            blocks.append({"id": b, "name": meta["name"], "desc": meta["desc"], "reads": meta["reads"],
                           "on": on, "why_off": "" if on else f"{meta['needs']} 자료가 아직 없음 — 생기면 켜짐"})
        old = next((o for o in cur["tabs"] if o["id"] == t["id"]), None)
        change = "새 탭" if not old else ("변경 없음" if old == t else "바뀜")
        rows.append({"id": t["id"], "label": t["label"], "change": change, "blocks": blocks})
    removed = [o for o in cur["tabs"] if o["id"] not in {t["id"] for t in new["tabs"]}]
    return {"tabs": rows, "removed": removed, "requests": new.get("requests", []),
            "writes": ["tabs.json"], "model_send": "없음 — 화면 조회는 모델을 부르지 않습니다",
            "rollback": "적용 직전 자동 백업 → python3 crm.py restore <백업ID>"}


def apply_tabs(data):
    p = pending_tabs_path(data)
    if not p.exists():
        raise CrmError("PREVIEW-NONE", "적용할 제안이 없습니다.", "python3 crm.py tabs propose 로 먼저 제안하세요.")
    new = json.loads(p.read_text(encoding="utf-8"))["tabs"]
    bid = backup(data, "before-tabs")
    with lock(data, "tabs apply"):
        save(data, "tabs.json", new)
    p.unlink()
    return bid


def cancel_tabs(data):
    p = pending_tabs_path(data)
    if p.exists():
        p.unlink()
        return True
    return False


def availability(data):
    data = Path(data)
    try:
        q = bool(load(data, "questions.json")["questions"])
    except CrmError:
        q = False
    try:
        c = bool(load(data, "candidates.json")["candidates"])
    except CrmError:
        c = False
    return {"questions": q, "candidates": c, "journal": any((data / "journal").glob("j-*.md"))}


# ── 다음 행동 ───────────────────────────────────────────

def _act(kind, text, command=None, target=None):
    return {"kind": kind, "target": target, "text": text, "command": command}


def next_action(data):
    """지금 할 일 하나. 읽기만 한다.
    {"kind", "target", "text", "command"} 를 돌려준다. text 는 화면에 그대로 쓰는 사람 말이라
    내부 ID·명령어를 넣지 않는다(기록·질문은 제목으로 부른다). 명령어는 command 에만 둔다."""
    data = Path(data)
    p = load(data, "profile.json")
    st = load(data, "setup-state.json").get("steps", {})
    qs = load(data, "questions.json")["questions"]
    tabs = load(data, "tabs.json")["tabs"]
    cands = load(data, "candidates.json")["candidates"]
    recs = list(data.glob("journal/j-*.md"))
    if st.get("env") != "done":
        return _act("setup", "환경 점검부터 해요. 점검 결과를 읽고 다음 조치 하나를 따르면 돼요.",
                    "python3 crm.py doctor")
    if not p["consent"].get("updated"):
        return _act("setup", "읽을 자료와 모델에 보낼 범위를 정해요. "
                             "Claude Code 에서 「crm-setup 스킬로 설정 시작」이라고 말하면 돼요.")
    if not qs and not recs:
        return _act("record", "질문은 나중에 정해도 돼요. 최근에 겪은 일을 한 편 적어 보세요.",
                    'python3 crm.py record add --title "…" --text "…"')
    if not tabs:
        return _act("tabs", "매일 볼 것(최대 3개)을 내 말로 탭 이름에 담아요.",
                    'python3 crm.py tabs propose --tab "이름=블록,블록"')
    if not recs:
        return _act("record", "기록을 한 편 적어 보세요.",
                    'python3 crm.py record add --title "…" --text "…"')
    used = {c["evidence"]["record"] for c in cands}
    fresh = [r for r in sorted(recs) if r.stem not in used]
    if qs and fresh and not cands:
        try:
            title = parse_journal(fresh[0])["title"]
        except (OSError, UnicodeDecodeError):
            title = "읽지 못한 기록"
        return _act("digest", f"「{title}」 기록에서 판단 후보를 뽑아요.",
                    f"python3 crm.py digest --record {fresh[0].stem} --question {qs[0]['id']}",
                    fresh[0].stem)
    pend = [c for c in cands if c["status"] == "pending"]
    if pend:
        return _act("review", f"아직 안 정한 문장이 {len(pend)}개 있어요. 원문과 대조해 남길지 정해요.",
                    f"python3 crm.py candidate confirm {pend[0]['id']}", pend[0]["id"])
    for q in qs:
        si = sheet_info(data, q["id"])
        if any(c["question"] == q["id"] for c in cands) and (not si["exists"] or si["stale"]):
            return _act("sheet", f"「{q['text']}」 한 장 파일을 새로 만들어요.",
                        f"python3 crm.py sheet build {q['id']}", q["id"])
    if st.get("reopen") != "done":
        return _act("reopen", "새 터미널에서 대시보드를 다시 열어 같은 내용이 보이는지 확인해요.",
                    "python3 crm.py serve 로 연 뒤 python3 crm.py setup done reopen")
    if st.get("drill") != "done":
        return _act("drill", "복구 리허설을 한 번 해 봐요. 점검·복구 가이드(guides/07)를 따라 하면 돼요.",
                    "python3 crm.py drill start")
    return _act("free", "기록을 한 편 더 쓰거나 질문·탭을 다듬어요.")


def next_line(act):
    """CLI 출력용 — 사람 말 한 줄, 명령이 있으면 다음 줄에."""
    return act["text"] + (f"\n  → {act['command']}" if act.get("command") else "")
