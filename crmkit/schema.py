"""설정 파일 검사. 각 함수는 오류 문장 목록을 돌려준다 — 빈 목록이면 정상."""
import re

from . import SCHEMA_VERSION

STATUSES = ("confirmed", "guessed", "empty", "off")       # 확인 / 추정 / 미입력 / 꺼 둠
STATUS_KO = {"confirmed": "확인", "guessed": "추정", "empty": "미입력", "off": "꺼 둠"}
CAND_STATUSES = ("pending", "confirmed", "excluded")
CAND_ORIGINS = ("self", "ai")
MAX_TABS = 3

# 블록 카탈로그. 여기 없는 블록은 만들지 않는다 — 카탈로그 밖 요구는 tabs.json 의 requests 로 남긴다.
BLOCKS = {
    "sheet": {"name": "질문별 한 장", "desc": "내 질문 하나에 연결된 확인한 판단과 근거 원문",
              "reads": ["questions.json", "sheets/"], "needs": "questions"},
    "candidates": {"name": "확인 대기 판단", "desc": "아직 내 판단이 아닌 후보 — 확인하거나 제외할 것",
                   "reads": ["candidates.json"], "needs": "candidates"},
    "journal": {"name": "최근 기록", "desc": "내가 쓴 기록 원문 목록과 본문",
                "reads": ["journal/"], "needs": "journal"},
    "questions": {"name": "내 질문", "desc": "계속 돌아오는 질문 목록",
                  "reads": ["questions.json"], "needs": "questions"},
    "next": {"name": "다음 행동", "desc": "지금 할 일 하나", "reads": [], "needs": None},
    "status": {"name": "실행 상태", "desc": "마지막 정리 실행의 성공·실패와 시각",
               "reads": [".crm/runs.json"], "needs": None},
}

ID_RE = {
    "question": re.compile(r"^q-\d{3,}$"),
    "source": re.compile(r"^s-\d{3,}$"),
    "tab": re.compile(r"^t-\d{3,}$"),
    "cand": re.compile(r"^c-\d{4,}$"),
    "journal": re.compile(r"^j-\d{8}-\d{2,}$"),
}


def _version(d, errs):
    v = d.get("schemaVersion")
    if v is None:
        errs.append("schemaVersion 이 없습니다")
    elif v > SCHEMA_VERSION:
        errs.append(f"schemaVersion {v} 은(는) 이 제품({SCHEMA_VERSION})보다 새 버전입니다 — 제품을 업데이트하세요")
    elif v < SCHEMA_VERSION:
        errs.append(f"schemaVersion {v} 은(는) 옛 버전입니다 — 업데이트 가이드의 마이그레이션 절차를 따르세요")


def profile(d):
    errs = []
    if not isinstance(d, dict):
        return ["profile.json 최상위가 객체가 아닙니다"]
    _version(d, errs)
    for k, f in (d.get("fields") or {}).items():
        if f.get("status") not in STATUSES:
            errs.append(f"profile.{k}.status 값이 이상합니다: {f.get('status')}")
    views = (d.get("fields") or {}).get("daily_views", {}).get("value") or []
    if len(views) > MAX_TABS:
        errs.append(f"매일 볼 것은 최대 {MAX_TABS}개입니다 (지금 {len(views)}개)")
    c = d.get("consent")
    if not isinstance(c, dict) or "model_send" not in c or "read_paths" not in c:
        errs.append("consent(읽기 범위·모델 전송 동의) 항목이 없습니다")
    return errs


def questions(d):
    errs = []
    _version(d, errs)
    seen = set()
    for q in d.get("questions", []):
        if not ID_RE["question"].match(q.get("id", "")):
            errs.append(f"질문 ID 형식 오류: {q.get('id')}")
        if q.get("id") in seen:
            errs.append(f"질문 ID 중복: {q.get('id')}")
        seen.add(q.get("id"))
        if not (q.get("text") or "").strip():
            errs.append(f"{q.get('id')} 질문 문장이 비었습니다")
    return errs


def sources(d):
    errs = []
    _version(d, errs)
    for s in d.get("sources", []):
        if not ID_RE["source"].match(s.get("id", "")):
            errs.append(f"자료 ID 형식 오류: {s.get('id')}")
        if not s.get("path"):
            errs.append(f"{s.get('id')} 경로가 비었습니다")
    return errs


def tabs(d):
    errs = []
    _version(d, errs)
    tl = d.get("tabs", [])
    if len(tl) > MAX_TABS:
        errs.append(f"탭은 최대 {MAX_TABS}개입니다 (지금 {len(tl)}개)")
    ids = set()
    for t in tl:
        if not ID_RE["tab"].match(t.get("id", "")):
            errs.append(f"탭 ID 형식 오류: {t.get('id')}")
        if t.get("id") in ids:
            errs.append(f"탭 ID 중복: {t.get('id')}")
        ids.add(t.get("id"))
        if not (t.get("label") or "").strip():
            errs.append(f"{t.get('id')} 탭 이름이 비었습니다")
        for b in t.get("blocks", []):
            if b not in BLOCKS:
                errs.append(f"{t.get('id')} 에 알 수 없는 블록: {b}")
    return errs


def candidates(d):
    errs = []
    _version(d, errs)
    seen = set()
    for c in d.get("candidates", []):
        cid = c.get("id", "")
        if not ID_RE["cand"].match(cid):
            errs.append(f"후보 ID 형식 오류: {cid}")
        if cid in seen:
            errs.append(f"후보 ID 중복: {cid}")
        seen.add(cid)
        if c.get("status") not in CAND_STATUSES:
            errs.append(f"{cid} 상태 값이 이상합니다: {c.get('status')}")
        if c.get("origin") not in CAND_ORIGINS:
            errs.append(f"{cid} 출처 값이 이상합니다: {c.get('origin')}")
        ev = c.get("evidence") or {}
        if not ev.get("record") or not ev.get("quote"):
            errs.append(f"{cid} 근거(기록 ID·인용)가 없습니다")
    return errs


def simple(d):
    errs = []
    _version(d, errs)
    return errs


VALIDATORS = {
    "meta.json": simple, "profile.json": profile, "questions.json": questions,
    "sources.json": sources, "tabs.json": tabs, "candidates.json": candidates,
    "setup-state.json": simple,
}
