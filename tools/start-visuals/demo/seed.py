"""데모 촬영용 시드 — 로컬 postgres 의 격리 스키마를 비우고 샘플 데이터를 넣는다. 운영 DB 에는 절대 붙지 않는다.

실행: server 의 파이썬(psycopg 가 있는 venv)으로. 보통은 shoot.py 가 부른다.
    DATABASE_URL=postgresql://localhost/crm_test <venv>/bin/python seed.py
가드: DATABASE_URL 호스트가 localhost·127.0.0.1 이 아니면 exit 1(CI 의 운영 DB 보호 가드와 같은 규칙).
격리: 스키마 CRM_TEST_SCHEMA(기본 start_demo)를 DROP → CREATE → init_schema. 연결 문자열에 search_path 를 얹어 쓴다.
값: 문장은 sample-data 와 같다(파일명 주석). sample-data JSON 을 파싱하지 않는다 — 상수로 둔다.
경로: MCP 도구와 같은 db.* 함수(question_add·record_add·candidates_propose·candidate_decide). 화면 날짜는 시드 뒤 2026-10-15 로 고정.
"""
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO = Path(__file__).resolve().parents[3]
SERVER = REPO / "server"
DEMO_USER = "demo@local"   # checks.py 메일 정규식에 안 걸리는 꼴(…@example.com 은 걸린다)

# sample-data/questions.json q-001
QUESTION = "새 도구를 팀에 들일 때 무엇부터 확인해야 하나"
# sample-data/journal/j-20261015-01.md (본문 그대로, 머리말·제목 줄 제외)
REC1_TITLE = "일정 도구 교체 회고"
REC1_BODY = """지난주 샘플팀에서 일정 관리 도구를 바꿨다. 도구 기능 비교에 이틀을 썼는데, 정작 문제는 누가 일정을 입력하느냐였다.
입력 담당이 정해지지 않은 상태에서는 어떤 도구를 골라도 같은 문제가 반복된다.

## 이게 어디에 붙나

- 질문: q-001
- 판단: 도구 비교보다 입력 담당을 먼저 정한다
- 다음: 다음 도입 회의 첫 안건을 입력 담당으로
- 출처: 회의 메모"""
REC1_SOURCE = "파일:메모.md"
# sample-data/journal/j-20261015-02.md
REC2_TITLE = "온보딩 문서 실험"
REC2_BODY = "신규 입사자 온보딩 문서를 체크리스트로 바꿨다. 질문이 줄었다고 느꼈지만 실제로 센 적은 없다. 다음 달에는 첫 주 질문 수를 세어 보기로 했다."
REC2_SOURCE = "직접 입력"
# sample-data/candidates.json c-0001(self, confirmed) · c-0002·c-0003(ai, pending)
CAND1 = {"text": "도구 비교보다 입력 담당을 먼저 정한다", "quote": "도구 비교보다 입력 담당을 먼저 정한다"}
CAND2 = [
    {"text": "새 방식을 들인 뒤에는 '줄었다'는 느낌 대신 실제로 센 값으로 효과를 확인해야 한다.",
     "quote": "질문이 줄었다고 느꼈지만 실제로 센 적은 없다."},
    {"text": "도입 효과를 판단하려면 먼저 셀 지표와 기간을 정해야 한다. 이 기록에서는 지표가 질문 수, 기간이 첫 주다.",
     "quote": "다음 달에는 첫 주 질문 수를 세어 보기로 했다."},
]
SHOW_DATE = "2026-10-15 22:37:00+09"


def guarded_url() -> tuple[str, str, str]:
    """(원 URL, 스키마, search_path 를 얹은 URL). 로컬 아니면 exit 1."""
    base = os.environ.get("DATABASE_URL", "")
    host = urlsplit(base).hostname
    if host not in ("localhost", "127.0.0.1"):
        print("실패: DATABASE_URL 호스트가 localhost·127.0.0.1 이 아니에요 — 촬영 중단")
        sys.exit(1)
    schema = os.environ.get("CRM_TEST_SCHEMA", "start_demo")
    if not schema.replace("_", "").isalnum():
        print("실패: CRM_TEST_SCHEMA 는 영문·숫자·_ 만")
        sys.exit(1)
    sep = "&" if "?" in base else "?"
    return base, schema, f"{base}{sep}options={quote('-csearch_path=' + schema)}"


def main():
    base, schema, url = guarded_url()
    import psycopg
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        c.execute(f'CREATE SCHEMA "{schema}"')
    os.environ["DATABASE_URL"] = url
    sys.path.insert(0, str(SERVER))
    import db
    db.init_schema()
    uid = db.user_by_email(DEMO_USER)["id"]
    # 동의 게이트(α2) — 촬영 서버(DEV)와 같은 규칙으로 guides 머리글에서 버전을 읽어 촬영 사용자를 동의시킨다(안 하면 /app 이 /consent 로 간다)
    import web
    db.set_consent_versions(web.read_consent_versions(web.GUIDES_DIR, dev=True))
    db.consent_grant(uid, dict(db.CONSENT_VERSIONS))
    q = db.question_add(uid, QUESTION)
    r1 = db.record_add(uid, REC1_TITLE, REC1_BODY, q["id"], source=REC1_SOURCE)
    r2 = db.record_add(uid, REC2_TITLE, REC2_BODY, q["id"], source=REC2_SOURCE)
    added1, _ = db.candidates_propose(uid, r1["id"], q["id"], [CAND1], "self")
    db.candidate_decide(uid, added1[0], "confirm")
    added2, dropped = db.candidates_propose(uid, r2["id"], q["id"], CAND2, "ai")
    assert len(added2) == 2 and not dropped, dropped
    with db.tx() as c:   # 화면 날짜 고정 — 이 격리 스키마 안에서만
        c.execute("UPDATE questions SET created=%s WHERE user_id=%s", (SHOW_DATE, uid))
        c.execute("UPDATE records SET created=%s WHERE user_id=%s", (SHOW_DATE, uid))
        c.execute("UPDATE candidates SET created=%s, updated=%s WHERE user_id=%s", (SHOW_DATE, SHOW_DATE, uid))
        c.execute("UPDATE candidate_history SET at=%s WHERE candidate_id = ANY(SELECT id FROM candidates WHERE user_id=%s)", (SHOW_DATE, uid))
    db.close()
    print(f"시드 끝 — 스키마 {schema}: 질문 1 · 기록 2 · 후보 3(내 판단 1, 대기 2) · 대기 후보 {added2}")


if __name__ == "__main__":
    main()
