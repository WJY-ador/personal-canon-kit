#!/usr/bin/env python3
"""지시 실행 워커 — 지시를 저장하면 뒷단이 바로 돈다.

사용: python3 scripts/run_worker.py --ts <배정 ts>

무인 경계 (CLAUDE.md 비용·외부모델 전제):
  판별은 이미 사람이 했다 — 사용자가 레인을 정하고 `ask` 를 썼다. 워커는 그 지시대로
  **읽어서 초안을 만들 뿐**이다.
  · 정본(career/·self.md·visionary/)을 절대 수정하지 않는다. 도구는 읽기 전용이고,
    파일을 쓰는 건 모델이 아니라 이 워커다 (검증한 본문만 out/ 에 떨군다).
  · 산출은 assignments.jsonl 레코드의 `result`(한 줄)·`result_body`(본문) 필드뿐 —
    별도 파일을 만들지 않는다 (더미 관리 제거, 2026-08-26). 결과는 업무 카드와 함께
    살고 카드가 지워지면 같이 사라진다. 정본 반영은 세션에서 사람이.
  · 출력은 스키마로 검증한다 — 실패하면 status 를 되돌려 다시 시킬 수 있게 둔다.
  · source == "feed" 는 원문+정본으로 넘긴다.
  · source == "map" 은 Personal 지도에서 시킨 것 — 지시(ask) 필수, 대상은 `path`가
    가리키는 데이터 루트(CANON_ROOT) 하위 파일 하나다. 그 파일 + 정본을 읽고 초안만 낸다 (2026-08-27).
  구독 CLI(claude -p) 범위 안. 종량제 키 사용 없음.
"""
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import worker_status, worker_claude   # noqa: E402
import models                         # noqa: E402  (모델 ID 한 자리 — scripts/models.json)
import checkpoint_store as store      # noqa: E402  (큐 쓰기는 여기로만 — flock+원자 교체)
import journal_writing
import grounding                      # noqa: E402  (질문 만들기 전에 쌓인 것 먼저 읽기 — 공통)
import canon_parse                    # noqa: E402  (정본 블록 파싱 한 자리 — 2026-09-17)
import refine                         # noqa: E402  (마감 계약 — 프롬프트·검증, 2026-09-17)

KST = timezone(timedelta(hours=9))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
ASSIGN = P / "_workspace/checkpoint/assignments.jsonl"
FEED = P / "visionary/linkedin/feed-queue.jsonl"
LINEAGE = P / ".runtime/.lineage-data.json"
TOOLS = ["Read", "Grep", "Glob", "ToolSearch", "mcp__docsearch__search_docs"]
# 반영안(apply)을 허용하는 정본 — 이 밖은 워커가 제안도 못 한다
APPLY_FILES = ["career/decisions.md", "career/work-decisions.md",
               "visionary/linkedin/insights.md"]
WRITING_WEB = ["WebSearch", "WebFetch"]   # 글쓰기 턴만 더한다 — 외부 수치는 URL 출처 필수 (2026-10-04)

APPLY_ONLY_PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 반영안 워커다.
아래는 이미 나온 실행 결과다. 이 결과에서 **정본에 붙일 한 줄**을 뽑아라.

**대상 글**
{title}

**지시였던 것**
{ask}

**나온 결과**
{body}

**정본 항목 목록**
{canon}

**절대 규칙**
- 어떤 파일도 수정하지 마라. 읽기만 한다 (Read·Grep·search_docs).
- 붙일 자리가 정말 없으면 apply 를 null 로 둔다 — 억지로 만들지 마라.
- 출력은 JSON 하나뿐. 설명·코드펜스 금지.

**출력 스키마**
{{"apply": {{"file": "career/decisions.md", "anchor": "헤딩 줄 그대로", "line": "붙일 한 줄"}},
  "why": "왜 거기인지 한 줄 (붙일 데 없으면 그 이유)"}}

{apply_rule}"""

APPLY_RULE = """**apply 규칙** — 정본에 붙일 한 줄의 반영안 (확신 있을 때만, 아니면 null):
- file 은 career/decisions.md, career/work-decisions.md,
  visionary/linkedin/insights.md 중 하나만.
- anchor 는 그 파일을 Read 로 **실제로 열어 확인한 헤딩 줄을 글자 그대로** 옮긴다
  (예: "### C-02 — ..."). 확인 없이 지어내면 삽입이 거부된다.
- line 은 이 글이 그 항목에 보태는 것 한 문장 + 출처(작성자·수집일). 원문이 실제로
  그 판단을 다룰 때만 — 추측 연결이면 apply 는 null."""


FOLLOWUP_PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 실행 워커다.
사용자가 앞선 결과를 보고 **이어서** 물었다. 이 질문에 답하라.

**이 대화가 시작된 자리**
{root_title}
처음 지시: {root_ask}

**지금까지의 흐름** (질문 → 그때 나온 답)
{history}

**이번 질문**
{ask}
{brief}
{grounded}

**절대 규칙**
- {rule}
- 어떤 파일도 수정하지 마라. 읽기만 한다 (Read·Grep·Glob·search_docs).
- 사용자 문장을 인용할 때는 정본에서 **찾아서 그대로** 옮기고 파일 경로를 밝힌다.
- 앞 턴에서 이미 답한 것을 되풀이하지 마라 — **이번 질문이 더한 것**만 낸다.
- 모르면 모른다고 쓴다. 출력은 JSON 하나뿐, 설명·코드펜스 금지.

**정본 항목 목록**
{canon}

**대상 글 (원문)**
{text}

**출력 스키마**
{{"summary": "이번 턴에서 무엇이 나왔는지 한 줄",
  "for_me": {{"from": "이 대화가 어디서 시작됐는지 한 줄 (원 글·최초 지시)",
              "so_far": ["앞선 턴 요약 — '무엇을 묻고 무엇이 나왔다' 한 줄씩, 최대 3개"],
              "answer": "이번 질문에 대한 답 — 2~3문장, 정본 용어로",
              "next": "다음에 하면 되는 일 한 줄"}},
  "apply": {{"file": "...", "anchor": "...", "line": "붙일 한 줄 — 120자 이내 + 출처"}},
  "body": "마크다운 본문 — 이번 질문의 실제 결과물. 인용에 출처 경로."}}

**길이** — 카드 한 줄에 들어가야 한다: summary 60자 이내, next 50자 이내, apply.line 120자 이내.

**for_me 규칙**
- so_far 는 **사용자가 그 카드를 안 펼쳐도 흐름이 잡히게** 쓴다. 앞 턴 결과를 그대로
  복사하지 말고 한 줄로 압축.
- answer 는 이번 질문에만 답한다. 근거·상세는 body 로.
- **정본에 반영하기 좋게** — answer 와 apply 는 어느 항목(C-XX·WD-XX)에 무엇을 보태는지
  드러나야 한다.

관련 대목을 찾을 때 `search_docs` MCP 툴을 먼저 쓴다.
{apply_rule}"""

# 2026-09-11 신규 진입 없음 — 옛 스레드 후속용. 피드 새 뿌리는 전부 lane "talk" 로 생기지만(/api/assign),
# 되묻기는 뿌리의 lane 을 복사하므로 옛 cross·canon·research·draft 스레드는 여기 프롬프트로 계속 이어진다.
LANE = {
    "cross": ("내 판단과 교차",
              "이 글을 사용자의 기존 판단과 교차시켜라. 산출은 visionary/linkedin/insights.md 에 "
              "붙일 **인용 조립** 형태다 — 사용자 본인 문장을 정본에서 찾아 그대로 인용하고, "
              "그 옆에 이 글이 무엇을 보태거나 반박하는지 한 줄씩 붙인다. AI 창작 문장으로 "
              "채우지 마라."),
    "draft": ("글로 쓰기",
              "journal의 본인 생각에서 글감 최대 3개를 찾고 대시보드 축으로 묶는다. "
              "외부 글은 보강 근거이며, 선택 뒤에는 필요한 빈칸만 묻고 본인 발화로 글을 연결한다."),
    "canon": ("근거로 박기",
              "이 글이 어느 C·WD 항목의 근거·반증이 되는지 판정하고, 그 항목 근거란에 붙일 "
              "**한 줄**을 제안하라. 항목마다: 코드, 관계(근거/반증/인접), 붙일 한 줄, 그렇게 "
              "본 이유. 원문이 실제로 그 판단을 다룰 때만 — 추측 연결 금지, 없으면 없다고 써라."),
    "research": ("사실 확인하기",
              "글의 주장을 사실 확인하라. 확인된 것 / 원 출처가 필요한 것 / 반증 가능성으로 "
              "나눠 적는다. 웹 접근이 없으므로 **무엇을 어디서 확인해야 하는지**를 구체적으로 "
              "지목하고, 사용자 정본에 이미 있는 반대 사례가 있으면 인용한다."),
}

# ── 이어서 묻기 (lane "talk", 2026-09-10 사용자: "요약된 글에서 추가 대화를 하고 싶은 건데 레인이 축도 아닌 것이 애매하다") ──
# 피드 [Claude] 의 기본. 레인 작업이 아니라 이 글을 놓고 사용자 질문에 답하는 대화다. 근거는 글 원문(source_text —
# 읽기 판과 같은 요점 번호)과 사용자 정본(search_docs). apply 는 항상 null — 정본 반영은 [완료]의 own_words 마감이 맡는다.
# 검증 계약은 valid() 의 후속 형식(for_me.answer·next·so_far) — 길이 상한은 프롬프트에 말하지 않았으니 걸지 않는다.
TALK_PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 실행 워커다. 사용자가 아래 글을 읽고 너와 **이어서 묻는 대화**를 한다.
레인 작업(정본 반영안·글감·근거 판정)은 시키지 않았다 — 사용자가 이번에 한 말에만 응답한다.

**지금까지** (첫 턴이면 없음)
{history}

**사용자의 이번 말**
{ask}

{grounded}

**먼저: 이번 말이 셋 중 무엇인지 정한다** (2026-09-18 — 사용자 "대화할 때 맥락을 못 잡는다")
1. **물음** ("N번이 무슨 뜻이야", "이 주장 근거가 뭐야") — 글 원문을 근거로 답한다. 글이 안 다루면 안 다룬다고 쓴다.
2. **시킴** ("1~3번 정리해줘", "표로 뽑아줘") — 그대로 수행한다.
3. **자기 생각·경험** ("나도 비슷한 경험이라 …인 것 같음", "이건 좀 아닌 듯") — **원문에 대한 주장이 아니라 사용자 본인의 판단이다.**
   글에 그 말이 없다고 반박하지 마라. 원문이 그 판단을 다루는지는 쟁점이 아니다. 대신:
   ① 사용자가 말한 판단을 그의 말 그대로 한 줄로 받는다 (요약·재해석 금지, 첫 문장은 부정으로 시작하지 않는다)
   ② `search_docs` 로 정본·일지에 같은 주제의 **본인 문장**을 찾아, 같으면 "이미 이렇게 적어뒀다", 다르면 어디가 갈리는지 인용으로 보인다
   ③ 글 원문은 그 판단을 뒷받침하거나 어긋나는 대목이 있을 때만 끌어온다

**절대 규칙**
- {rule}
- 어떤 파일도 수정하지 마라. 읽기만 한다 (Read·Grep·Glob·search_docs).
- **물음·시킴**일 때 답의 근거는 아래 글 원문이 첫째다 — 원문이 다루지 않는 것을 추측으로 채우지 마라.
- 사용자의 판단·정본(C-XX·WD-XX·journal·insights)과 닿는 대목이 있으면 `search_docs` 로 찾아 **사용자가 실제로 쓴 문장을
  그대로** 인용하고 파일 경로를 밝힌다. 없으면 억지로 잇지 마라.
- 사용자가 화면에서 본 요점 번호("N번")는 아래 원문의 같은 번호다. 원문에 없는 문단 번호를 만들지 마라.
- 답 끝에 되묻고 싶은 것이 있으면 한 문장 물어도 된다 — 강제는 아니다. 찬반("동의하나요?")은 묻지 마라.
- 출력은 JSON 하나뿐. 설명·코드펜스 금지.

**정본 항목 목록** (인용할 때 코드·제목 참고)
{canon}

**대상 글 (원문)**
{text}

**출력 스키마**
{{"summary": "이번 답 한 줄 (카드에서 보는 문장)",
  "for_me": {{"answer": "사용자의 이번 말에 대한 응답 2~5문장. 물음·시킴이면 글의 어느 대목에서 그렇게 보는지 포함, 자기 생각이면 그 판단을 받는 문장으로 시작",
              "so_far": ["앞선 턴이 있으면 '무엇을 묻고 무엇이 나왔다' 한 줄씩, 최대 3개 — 첫 턴이면 빈 배열"],
              "next": "사용자가 다음에 물어볼 만한 것 또는 확인할 것 한 줄"}},
  "apply": null,
  "body": "마크다운 본문 — 답의 상세. 글 인용은 요점 번호나 문장 그대로, 정본 인용에는 파일 경로. 80자 이상."}}

**형식 규칙 (어기면 결과 전체가 버려진다)** — `for_me` 의 키는 **answer·so_far·next 셋뿐**. `question`·`points` 키는 쓰지 않는다.
`apply` 는 항상 null. JSON 을 한 번에 끝까지 낸다 — 도구를 더 쓰고 싶으면 JSON 을 내기 전에 쓴다.

관련 대목을 찾을 때 `search_docs` MCP 툴을 먼저 쓴다 (데이터 루트 스코프)."""

PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 실행 워커다.
사용자가 아래 글을 "{lane_name}" 레인으로 분배하고 지시를 내렸다. 그 지시를 수행하라.

**사용자의 지시**
{ask}

**레인의 기본 작업**
{lane_task}
{brief}
{grounded}

**절대 규칙**
- {rule}
- 어떤 파일도 수정하지 마라. 읽기만 한다 (Read·Grep·Glob·search_docs).
- 사용자 문장을 인용할 때는 정본에서 **찾아서 그대로** 옮기고 파일 경로를 밝힌다. 지어내지 마라.
- 원문이 다루지 않는 것을 추측으로 연결하지 마라. 모르면 모른다고 쓴다.
- 출력은 JSON 하나뿐. 설명·코드펜스 금지.

**정본 항목 목록**
{canon}

**대상 글 (원문)**
{text}

**출력 스키마**
{{"summary": "무엇을 했는지 한 줄 (사용자가 큐에서 볼 문장)",
  "for_me": {{"question": {{"q": "사용자에게 던지는 역질문 한 문장 — 물음표로 끝난다",
                            "quote": "사용자가 정본에 실제로 쓴 문장 그대로",
                            "source": "그 문장이 있는 파일 경로"}},
              "next": "사용자가 다음에 하면 되는 일 한 줄"}},
  "apply": {{"file": "career/decisions.md", "anchor": "정본에 실제로 있는 그 항목의 헤딩 줄 그대로",
             "line": "그 항목에 붙일 한 줄 (출처 포함, 200자 이내)"}},
  "body": "마크다운 본문 — 위 지시의 실제 결과물. 인용에는 출처 경로를 붙인다."}}

{apply_rule}

**for_me 규칙** — 본문 요약·논평이 아니라 **사용자에게 던지는 역질문 하나**다:
- for_me 안의 키는 **question·next 둘뿐**이다. `points`·`next_options` 키는 폐기됐다 —
  쓰면 결과 전체가 버려진다. 판단 재료는 body 에 쓴다. 사용자의 답이 곧 데이터로
  쌓이므로, 답하면 정본이 자라는 질문이어야 한다.
- question.q 는 이 글이 사용자의 판단을 건드리는 자리에서 갈림길을 드러내고 묻는다.
  글과 정본이 **부딪히면** 그 긴장을 묻고 (어느 쪽이 맞으면 무엇이 달라지나),
  **같은 결이면** 그 판단을 굳히거나 뒤집을 다음 확인을 묻고,
  정본에 **없는 것이면** 그게 새 항목감인지 스치는 소식인지 가르는 질문을 낸다.
- 찬반을 묻지 마라. "동의하나요?" 금지 — 그 문장이 참일 때 무엇이 달라지는지 묻는다.
- q 는 **물음표로 끝나는 한 문장**이다. 배경 설명·근거 나열을 질문 앞에 붙이지 마라 —
  근거는 quote 가, 상세는 body 가 말한다. 두 문장이 되면 계약 위반이다.
- question.quote 는 **사용자가 정본에 실제로 쓴 문장을 찾아 그대로** 옮긴다
  (search_docs·Read). 지어내는 순간 계약 위반이다. source 에 파일 경로.
  정본에 닿는 문장이 정말 없으면 원문 글의 문장을 그대로 옮기고 source 는 "원문".
- **원문 문단 번호("10번 문단" 등) 인용 금지** — 사용자는 그 번호를 모른다. 단 "요점 N번"은
  사용자가 화면에서 보는 번호이므로 그대로 쓴다. 내용을
  한 단어로 지칭하라 (예: "CWA 선언", "복잡성 증폭 진단").
- 반드시 **사용자의 정본 용어**로 쓴다 — C-XX·WD-XX 코드와 그 제목, 사용자 본인의 표현
  (예: "가격표", "만들지 않는 판단"). 코드 뒤 괄호 설명은 세 어절 이내.
- next 는 명령문 한 줄 — 무엇을 읽고 무엇을 결정하면 되는지.

관련 대목을 찾을 때 `search_docs` MCP 툴을 먼저 쓴다 (데이터 루트 + current-work.md 자료로 선언된 프로젝트 폴더)."""

MAP_PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 실행 워커다.
사용자가 자기 워크스페이스 지도에서 파일 하나를 골라 지시를 내렸다. 그 지시를 수행하라.

**대상**
{path}

**사용자의 지시**
{ask}

**절대 규칙**
- 어떤 파일도 수정하지 마라. 읽기만 한다 (Read·Grep·Glob·search_docs).
- 대상이 파일이면 Read 로 **끝까지** 읽고 시작한다 — 아래 발췌는 앞부분일 뿐이다.
  대상이 폴더면 아래 목록은 각 글의 머리뿐이다. 판단에 필요한 글은 Read 로 직접 연다.
- 폴더를 다룰 때는 파일을 하나씩 나열하지 말고 **주제·시기·반복되는 판단으로 묶어**
  구조화한다. 묶음마다 어느 글들이 거기 속하는지 파일명으로 밝힌다.
- 사용자 문장을 인용할 때는 **찾아서 그대로** 옮기고 파일 경로를 밝힌다. 지어내지 마라.
- 우선순위를 매길 때는 파일 안의 `열린 질문`·`⚠️AI`·`🌱` 표시를 먼저 본다 — 그것들이
  여물지 않은 자리다.
- {rule}
- 모르면 모른다고 쓴다. 출력은 JSON 하나뿐. 설명·코드펜스 금지.

**정본 항목 목록**
{canon}

{grounded}

**대상 파일 (앞부분 발췌)**
{text}

**출력 스키마**
{{"summary": "무엇이 나왔는지 한 문장 — **60자 이내**, 괄호 설명 금지",
  "for_me": {{"question": {{"q": "나에게 던지는 역질문 한 문장 — **60자 이내**",
                            "quote": "그 질문의 근거 — 내가 실제로 쓴 문장 그대로",
                            "source": "그 문장이 있는 파일 경로",
                            "why": "왜 지금 이 질문인가 — **40자 이내** 한 줄"}},
              "next": "다음에 할 일 한 문장 — **50자 이내**, 명령문"}},
  "apply": {{"file": "career/decisions.md", "anchor": "정본에 실제로 있는 헤딩 줄 그대로",
             "line": "붙일 한 줄 — **120자 이내 한 문장** + 출처"}},
  "body": "정리된 본문 — 아래 body 형식을 그대로 지킨다."}}

**body 형식** — 한 화면에서 읽히게. 길게 쓰지 마라:
- 소제목은 `## ` 로 **최대 3개**. 소제목 밑은 불릿 3~5개.
- 불릿 한 줄 = 한 문장, **80자 이내**. 겹불릿(들여쓴 불릿) 금지.
- 굵게(`**`)는 소제목 밑 첫 단어 한 번까지. 표·코드블록·이모지 금지.
- 인용은 `> ` 한 줄로, 뒤에 출처 파일명. 원문을 길게 옮기지 마라.
- 전체 25줄을 넘기지 마라. 넘칠 것 같으면 항목을 줄이지 말고 **문장을 줄인다**.
- 마지막 소제목은 항상 `## 지금 할 것` — 번호 매긴 1~3개, 각 한 문장.

{apply_rule}

**for_me 규칙** — 요약·논평이 아니라 **사용자에게 던지는 역질문 하나**다:
- for_me 안의 키는 **question·next 둘뿐**이다. `points`·`next_options` 키는 폐기됐다 —
  쓰면 결과 전체가 버려진다. 판단 재료는 body 에 쓴다. 사용자의 답이 곧 데이터로
  쌓이므로, 답하면 정본이 자라는 질문이어야 한다.
- question.q 는 사용자의 글에서 확신이 끊긴 자리를 캐는 질문. 타겟 우선순위 —
  ① 주저 마커 ("흠"·"근데"·"~같은데" 등 확신이 끊긴 문장)
  ② 버린 대안 (채택만 남고 왜 기각했는지가 안 남은 것)
  ③ 전제 (거짓이면 무너지는 가정 — 갈림길을 드러내고 어느 쪽인지 묻는다).
- 찬반을 묻지 마라. 그 문장이 참일 때 무엇이 달라지는지, 어느 갈림길인지 묻는다.
- q 는 **물음표로 끝나는 한 문장**이다. 배경 설명·근거 나열을 질문 앞에 붙이지 마라 —
  근거는 quote 가, 상세는 body 가 말한다. 두 문장이 되면 계약 위반이다.
- question.quote 는 **사용자가 실제로 쓴 문장을 찾아 그대로** 옮긴다 (Read·search_docs).
  못 찾으면 질문을 바꾼다 — 지어내는 순간 계약 위반이다. source 에 파일 경로.
- next 는 명령문 한 줄.
- **행 번호·문단 번호로 지칭 금지** ("87행" 금지) — 사용자는 그 번호를 모른다.
  그 자리의 **실제 문장을 짧게 그대로 인용**해 지칭한다. next 안의 지칭도 같다.

관련 대목을 찾을 때 `search_docs` MCP 툴을 먼저 쓴다 (데이터 루트 + current-work.md 자료로 선언된 프로젝트 폴더)."""


def _file_brief(f, chars=260):
    """파일 한 편의 머리 — 첫 헤딩 + 앞부분 발췌 (frontmatter 건너뜀)."""
    try:
        lines = f.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    i = 0
    if lines and lines[0].strip() == "---":            # frontmatter 스킵
        for j in range(1, min(len(lines), 40)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    head = next((l[2:].strip() for l in lines[i:i + 30] if l.startswith("# ")), "")
    body = " ".join(l.strip() for l in lines[i:] if l.strip() and not l.startswith("#"))
    return f"### {f.name} — {head}\n{body[:chars]}"


def map_file_text(a, limit=6000, files_cap=25):
    """map 건의 대상 — 데이터 루트(CANON_ROOT) 하위만. 파일이면 본문, 폴더면 하위 글 목록+발췌."""
    raw = (a.get("path") or "").strip()
    if not raw:
        return None, "path 없음"
    target = (P / raw).resolve()
    if not str(target).startswith(str(P.resolve())):
        return None, "Personal 밖 경로"
    if target.is_dir():
        files = sorted((f for f in target.glob("*.md") if not f.name.startswith(".")),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None, "폴더에 md가 없다"
        shown = files[:files_cap]
        head = (f"(폴더 {raw} — md {len(files)}개, 최신 {len(shown)}개의 머리만 아래에 실었다. "
                f"필요한 글은 Read 로 직접 열어라.)\n\n")
        return head + "\n\n".join(_file_brief(f) for f in shown), None
    try:
        return target.read_text(errors="replace")[:limit], None
    except OSError as e:
        return None, f"읽기 실패: {e}"


def rows(path):
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except OSError:
        return []


# ── 열린 질문 대화 (2026-09-10 사용자: "⚠️AI 규칙을 없애고 대화를 통해 내 언어로 채우는 것") ──
# 구조 탭 열린 질문 서랍의 [Claude와 이어서]. 워커는 **묻기만** 한다 — 해소 절차(work-decisions.md 헤더)의
# "찬반 금지, 본인 언어로 다시 말하게 하는 질문, 한 번에 하나". A+B 섞기(사용자 2026-09-10): 질문 하나 + 갈림길 0~3개.
# 갈림길은 방향(40자 안)만 — 완성 문장이면 사용자가 그걸 눌러 제출하게 되고 그게 곧 AI 문장이다.
# 마무리(--close)는 `refine` 계약 하나다 (2026-09-17) — 서버가 발급한 목적지 후보에 정본 문장을 쓴다.
CANON_PATHS_W = {"C": P / "career/decisions.md", "WD": P / "career/work-decisions.md",
                 "AC": P / "career/application-craft.md"}

OPENQ_PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 실행 워커다. 사용자가 자기 정본의 **열린 질문**에 답하려고
너와 대화를 시작했다. 네 일은 **답을 대신 쓰는 것이 아니라, 사용자가 자기 문장으로 답을 끝까지 말하게 묻는 것**이다.
마지막에 정본에 들어가는 글은 이 대화에서 사용자가 친 문장만으로 조립된다 — 네가 쓴 글자는 한 자도 안 들어간다.

**대상 결정** {code}
{block}

**열린 질문** (큐 원문)
{question}
{journal}
**지금까지** (네 질문 → 사용자의 답)
{history}

{grounded}

**사용자의 이번 답**
{ask}

**절대 규칙**
- 파일 수정 금지. 읽기만 (Read·Grep·Glob·search_docs).
- **질문은 한 번에 하나.** 찬반을 묻지 마라("동의하나요?" 금지). 사용자 답이 참일 때 무엇이 달라지는지, 그 판정을 어디서
  봤는지(장면·화면·사건), 반대 사례는 언제였는지처럼 **본인 언어로 다시 말하게 하는** 질문만.
- 장면(as-is → 무엇을 봤나 → 무엇이 바뀌었나)을 수치보다 먼저 묻는다. 사용자 자리(현업 동선)에서 묻는다.
- 앞 턴에서 이미 답한 것을 다시 묻지 마라. {rule}
  그러고도 남는 **비어 있는 자리**(판정 기준·장면·예외·결과) 하나만 고른다.
- 사용자 답이 이미 질문에 충분히 닿았으면 q 는 "더 보탤 것이 없으면 [완료]를 누르면 된다 — 남은 빈자리가 있나?" 류로
  **끝낼 수 있음을 알린다**. 억지로 늘리지 마라.
- 출력은 JSON 하나만. 설명·코드펜스 금지.

**출력 스키마**
{{"summary": "이번 턴 한 줄 — 사용자 답에서 무엇이 잡혔고 무엇이 비었나 (60자 이내)",
  "for_me": {{"question": {{"q": "사용자에게 던지는 질문 한 문장 — 물음표로 끝난다, 200자 이내",
                            "quote": "이 질문의 발판 — 사용자가 **이 대화에서 친 문장** 또는 정본 블록의 문장을 그대로",
                            "source": "대화 | 파일 경로"}},
              "options": [{{"label": "갈림길 방향 — 40자 이내, 명사구·방향만, 완성 문장 금지"}}],
              "next": "사용자가 다음에 하면 되는 일 한 줄 (50자 이내)"}},
  "apply": null,
  "body": "왜 이걸 묻나 — 사용자 답에서 잡힌 것 / 아직 빈 자리 / 정본 블록·일지에서 본 근거(경로). 80자 이상."}}

**형식 규칙 (어기면 결과 전체가 버려진다)** — `for_me` 의 키는 **question·options·next 셋뿐**. `question` 은 반드시
**객체** `{{"q","quote","source"}}` 다 — 문자열로 쓰거나 quote·source 를 for_me 바로 밑에 두면 버려진다. `apply` 는 항상 null.
JSON 을 한 번에 끝까지 낸다 — 도구를 더 쓰고 싶으면 JSON 을 내기 전에 쓴다.

**options 규칙** — 사용자가 막힌 신호(한두 단어 답·"모르겠다"·되물음)가 읽히거나 질문이 진짜 갈림길일 때만 2~3개.
아니면 빈 배열. 각 label 은 방향을 가리키는 짧은 구(예: "현업이 못 굴린 화면", "요청 빈도로 판정")이고 문장이 아니다.
사용자가 label 을 그대로 제출하면 그 문장은 마무리에서 **버려진다** — 그러니 label 은 답이 아니라 손잡이다.

관련 대목은 `search_docs` 먼저 (데이터 루트 + current-work.md 자료로 선언된 프로젝트 폴더)."""

def canon_block(code, full=True):
    """C-XX/WD-XX/AC-XX 항목 블록 본문 — 파싱은 `canon_parse` 한 자리 (2026-09-17).

    옛 구현은 2500자에서 잘랐다. 열린 질문 대화는 그 항목 전체를 근거로 물어야 해서 전문을 준다
    (설계 ②: 서버·워커가 같은 파서로 같은 블록을 본다).
    """
    if not canon_parse.prefix_of(code):
        return "(코드 없음)"
    return canon_parse.block_text(code, P, full=full)


def openq_history(seq, ask):
    """(Claude 질문 → 내 답) 쌍 — 부모 사슬 seq 의 for_me.question 과 그 다음 rec 의 ask 로 잇는다."""
    if not seq:
        return "(첫 턴)"
    out = []
    for i, r in enumerate(seq):
        q = ((r.get("for_me") or {}).get("question") or {}).get("q") or (r.get("result") or "")
        if i == 0 and (r.get("ask") or "").strip():
            out.append(f"0. 내 첫 답: {r['ask'].strip()}")
        nxt_ask = seq[i + 1].get("ask") if i + 1 < len(seq) else ask
        out.append(f"{i + 1}. Claude 질문: {q.strip()}\n   내 답: {(nxt_ask or '').strip()}")
    return "\n".join(out)


def openq_prompt(a, seq):
    """열린 질문 턴 프롬프트 — 뿌리(root)의 code·question 을 쓴다. 일지 후보는 brief_worker.journal_candidates 재사용."""
    root = seq[0] if seq else a
    code = (root.get("code") or "").strip()
    question = (root.get("question") or root.get("title") or "").strip()
    import brief_worker
    cands = brief_worker.journal_candidates([code]) if code else []
    journal = ""
    if cands:
        journal = "\n**먼저 볼 일지 후보** — 계보에서 이 결정에 '어디에 붙나'로 닿은 날 (기계 후보, 본인 확정 아님)\n" + \
                  "\n".join(f"- {d} {pth} ({', '.join(cs)})" for d, pth, cs in cands[:6]) + "\n"
    les = root.get("lesson") or {}
    if root.get("origin") == "lesson" and les:
        # 가르침 카드에서 온 질문 — 그 글이 준 규칙·전후 예를 같이 준다 (2026-09-10). 문안을 쓰라는 뜻이 아니다: 묻기만.
        journal = ("\n**이 질문이 나온 가르침** (%s 글 — 남의 글이다, 인용만)\n" % (les.get("room") or "")) + \
                  "".join("- 규칙: %s\n" % r for r in (les.get("rules") or [])) + \
                  (("- 나쁜 예: %s\n- 좋은 예: %s\n" % (les.get("before"), les.get("after"))) if les.get("after") else "") + \
                  "".join("- 내 재료(%s): %s\n" % (m.get("path"), m.get("quote")) for m in (les.get("materials") or [])) + \
                  "\n이 규칙을 사용자 자신의 장면에 앉히게 **묻는다**. 사용자 대신 문장을 쓰지 마라.\n" + journal
    return OPENQ_PROMPT.format(code=code or "(코드 없음)", block=canon_block(code) if code else "(대상 결정 없음 — 큐 파일 '답한 것'에만 남는 질문)",
                               question=question, journal=journal,
                               rule=grounding.RULE,
                               grounded=grounded_block([pth for _, pth, _ in cands[:3]],
                                                       text="\n".join(x for x in (question, root.get("title"),
                                                                                  root.get("slot"), a.get("ask")) if x),
                                                       code=code or None, slot=root.get("slot") or None,
                                                       thread_id=root.get("ts"), exclude_ts=a.get("ts"),
                                                       targets=[root.get("id"), root.get("path")]),
                               history=openq_history(seq, a.get("ask")), ask=(a.get("ask") or "").strip())


def _norm_words(t):
    import re as _re
    return _re.sub(r"\s+", " ", _re.sub(r"[*_`\"“”'‘’]", "", t or "")).strip()


def is_talk(root):
    """피드 '이어서 묻기' 스레드 — 실행 프롬프트(TALK_PROMPT)·apply 없음 분기용."""
    return root.get("source") == "feed" and root.get("lane") == "talk"


def canon_list():
    try:
        d = json.loads(LINEAGE.read_text())
    except (OSError, json.JSONDecodeError):
        return "(색인 없음)"
    return "\n".join(f"- {n['id']} {n.get('maturity','')} {n.get('title','')}"
                     for n in d.get("nodes", []) if n.get("type") in ("C", "WD"))


def chain(a, rows_all):
    """이 배정까지 온 대화 사슬 (뿌리 → ... → 부모). 후속이면 비어 있지 않다."""
    by = {r["ts"]: r for r in rows_all}
    seq, cur = [], a
    while cur.get("parent_ts") and by.get(cur["parent_ts"]):
        cur = by[cur["parent_ts"]]
        seq.append(cur)
    return list(reversed(seq))


def source_text(item_id):
    """실행 워커가 보는 '대상 글' = 사용자가 읽기 판에서 본 것과 같은 화면 (2026-09-08).
    요점(long)이 있으면 as-is/to-be/운영 → 번호 붙은 요점 → 댓글 답 → 내 축과 → 원문 순으로,
    화면과 같은 번호로 넣는다. 실측: 사용자가 요점 "5번"을 가리켰는데 워커는 원문만 봐서 다른 문장을 집었다."""
    for r in rows(FEED):
        if r.get("id") != item_id:
            continue
        text = r.get("body") or r.get("text", "")
        L = r.get("long") or {}
        if not isinstance(L, dict) or not L.get("points"):
            return text
        out = ["**사용자가 화면(읽기 판)에서 본 요점 — 번호는 화면 그대로. 사용자가 \"N번\"이라 하면 이 요점 번호다**"]
        if L.get("asis") or L.get("tobe"):
            out.append(f"as-is: {L.get('asis') or '—'}\nto-be: {L.get('tobe') or '—'}"
                       + (f"\n운영: {L.get('ops')}" if L.get("ops") is not None else ""))
        out.append("요점\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(L["points"])))
        if L.get("answers"):
            out.append("댓글이 답한 것\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(L["answers"])))
        if L.get("numbers"):
            out.append("숫자·산식: " + " · ".join(L["numbers"]))
        if L.get("axis_note"):
            out.append(f"내 축과: {L['axis_note']}")
        out.append("**원문**\n" + text)
        return "\n\n".join(out)
    return ""


def ext_sources_block(root, selected=None):
    """직접 붙인 수집글과 선택한 경험의 추천 자료를 보강용으로 전달한다.
    선택한 글감의 자료만 넣고 원문은 6000자까지. 자료가 없으면 빈 문자열."""
    ext = list(root.get("ext_sources") or [])
    if selected:
        ext = [e for e in ext if e.get("topic") == selected["index"]]
        seen = {e["id"] for e in ext}
        ext += [{"topic": selected["index"], **s} for s in selected["topic"].get("support", []) if s["id"] not in seen]
    if not ext:
        return ""
    # 수집글 + 좋아요·리포스트 세 파일에서 id 로 찾는다 (2026-10-04). src 없는 옛 항목은 수집글부터.
    index = journal_writing.source_index(P)
    parts = []
    for e in sorted(ext, key=lambda x: (x.get("topic", 0), x.get("added_at", ""))):
        r = journal_writing.lookup_source(index, e.get("id"), e.get("src"))
        if not r:
            continue
        parts.append(f"[글감 {e.get('topic', 0) + 1} 보강 자료 · {r['label']} — 남의 글, 내 발화 아님] "
                     f"{r['author']} — {r['summary'] or r['text'][:80]}\n"
                     f"출처: {r['url'] or e.get('id')}\n관계 제안: {e.get('role', '')} {e.get('why', '')}\n"
                     f"{r['text'][:6000]}\n(저장 원문 일부 — 잘린 경우 파일에서 확인)")
    return "\n\n".join(parts)


def grounded_block(paths=None, text="", code=None, slot=None, budget=6500,
                   thread_id=None, targets=None, exclude_ts=None):
    """질문을 만들기 전에 읽을 것 — 이미 답한 것 + 재료·프로젝트 문서 (grounding 모듈).

    2026-09-17: 대시보드가 이미 일지·프로젝트 문서에 적혀 있는 것을 다시 물어 사용자가 같은 답을
    두 번 타이핑했다. 프롬프트 한 칸으로 그 자료를 통째로 넣어 "빈자리만 묻기"를 가능하게 한다.
    2026-09-17 2차: code/slot 이 없는 자리(이어서 묻기·후속·지도)가 R1 의 사각지대였다 —
    **스레드 뿌리 ts** 와 목적지(파일 경로·글 id·코드)를 넘겨 그 스레드에서 이미 한 답을 찾게 한다.
    """
    try:
        return grounding.grounding_block(paths or [], code=code, slot=slot,
                                         budget=budget, text=text or None,
                                         thread_id=thread_id, targets=targets,
                                         exclude_ts=exclude_ts)
    except OSError:
        return ""


def brief_block(a):
    """정리 워커(brief_worker)가 이미 엮어둔 것 + 사용자가 카드에서 내린 판정을 프롬프트 한 칸으로.

    같은 인용을 두 워커가 따로 찾던 중복을 없애고(2026-09-02), 사용자가 '아니다'라고 한
    연결은 빼고, '뒤집기'(take)는 사용자 판정으로 실린다. brief 없는 옛 건은 빈 문자열.
    """
    b = a.get("brief")
    if not isinstance(b, dict):
        return ""
    fix = a.get("brief_fix") or {}
    rej_dates, rej_codes = set(fix.get("scene") or []), set(fix.get("links") or [])
    out = ["", "**정리 워커가 미리 엮어둔 것** (같은 인용을 다시 찾지 말고 이어서 쓴다)"]
    sc = b.get("scene")
    if isinstance(sc, dict) and sc.get("quote") and sc.get("date") not in rej_dates:
        out.append(f"- 내 일지 {sc.get('date','')}: “{sc['quote']}” ({sc.get('source','')})")
        if sc.get("then"):
            out.append(f"  이 글이 더하는 것: {sc['then']}")
    if b.get("fork"):
        out.append(f"- 갈림길: {b['fork']}")
    d = b.get("default") or {}
    take = a.get("take") or {}
    if take.get("verdict") == "flipped" and take.get("text"):
        out.append(f"- 기본 판정(정리 워커): {d.get('verdict','')}")
        out.append(f"- **사용자가 뒤집었다** — 이 판정을 따른다: {take['text']}")
    elif d.get("verdict"):
        out.append(f"- 기본 판정 (사용자가 뒤집지 않음): {d['verdict']}")
    if b.get("claim"):
        out.append(f"- 이 글이 말하는 것: {b['claim']}")
    links = [l for l in (b.get("links") or []) if isinstance(l, dict)
             and l.get("code") not in rej_codes]
    for l in links:
        q = f" — “{l['quote']}” ({l.get('source','')})" if l.get("quote") else ""
        out.append(f"- {l.get('code','')} {l.get('rel','')}: {l.get('note','')}{q}")
    if rej_codes or rej_dates:
        out.append("- 사용자가 아니라고 한 연결 (쓰지 마라): "
                   + ", ".join(sorted(rej_dates | rej_codes)))
    if b.get("tension"):
        out.append(f"- 걸리는 지점: {b['tension']}")
    return "\n".join(out) + "\n"


def patch(ts, **fields):
    """잠금 아래 병합 — 도는 사이 사용자·다른 워커가 쓴 걸 덮지 않는다 (checkpoint_store)."""
    store.patch(ts, **fields)


def valid(o, tight=False):
    """코드 계약 — 통과 못 하면 폐기한다. tight=True 면 길이 상한까지 본다 (지도 건)."""
    if tight and isinstance(o, dict):
        if isinstance(o.get("summary"), str) and len(o["summary"].strip()) > 90:
            return False
        fm0 = o.get("for_me")
        if isinstance(fm0, dict) and isinstance(fm0.get("next"), str) and len(fm0["next"]) > 80:
            return False
        ap0 = o.get("apply")
        if isinstance(ap0, dict) and isinstance(ap0.get("line"), str) and len(ap0["line"]) > 180:
            return False
    if not (isinstance(o, dict)
            and isinstance(o.get("summary"), str) and o["summary"].strip()
            and isinstance(o.get("body"), str) and len(o["body"].strip()) >= 80):
        return False
    fm = o.get("for_me")
    if fm is not None:
        def _pt(x):
            if isinstance(x, str):        # 구형 호환
                return bool(x.strip())
            return (isinstance(x, dict)
                    and x.get("k") in ("붙는다", "부딪힌다", "새 조각", "주의")
                    and isinstance(x.get("t"), str) and 0 < len(x["t"].strip()) <= 90)
        if not isinstance(fm, dict) or not isinstance(fm.get("next"), str):
            return False
        if "answer" in fm or "so_far" in fm:      # 후속(대화) 형식
            if not (isinstance(fm.get("answer"), str) and fm["answer"].strip()):
                return False
            sf = fm.get("so_far")
            if sf is not None and not (isinstance(sf, list) and len(sf) <= 4
                                       and all(isinstance(x, str) for x in sf)):
                return False
        elif isinstance(fm.get("question"), dict):   # 역질문 형식 (map, 2026-08-27)
            qq = fm["question"]
            if not (isinstance(qq.get("q"), str) and 0 < len(qq["q"].strip()) <= 200
                    and isinstance(qq.get("quote"), str) and qq["quote"].strip()
                    and isinstance(qq.get("source"), str) and qq["source"].strip()):
                return False
        elif not (isinstance(fm.get("points"), list) and 1 <= len(fm["points"]) <= 4
                  and all(_pt(x) for x in fm["points"])):
            return False
        no = fm.get("next_options")
        if no is not None and not (isinstance(no, list) and len(no) <= 4
                                   and all(isinstance(x, str) and x.strip() for x in no)):
            return False
        op = fm.get("options")   # 열린 질문 갈림길 (2026-09-10) — 0~3개, label 은 60자 안의 구
        if op is not None and not (isinstance(op, list) and len(op) <= 3
                                   and all(isinstance(x, dict) and isinstance(x.get("label"), str)
                                           and 0 < len(x["label"].strip()) <= 60 for x in op)):
            return False
    ap = o.get("apply")
    if ap is not None:
        if not (isinstance(ap, dict)
                and isinstance(ap.get("file"), str)
                and isinstance(ap.get("anchor"), str) and ap["anchor"].strip()
                and isinstance(ap.get("line"), str)
                and 0 < len(ap["line"].strip()) <= 250):
            return False
        if ap["file"] not in APPLY_FILES:
            return False
    return True


def refine_close(root, thread, text, model):
    """[완료] 마감 — 대화를 **정본 문장 + 경위**로 가공한다 (설계 ③, 2026-09-17 R4).

    옛 마감 둘(사용자 턴 문장 고르기, 결론 요약)을 `refine.PROMPT` 하나로 합쳤다. 목적지·근거
    후보는 **서버가 스레드에 실어 둔 것**(root.targets·root.close_pool)을 읽는다 — 워커가 경로를
    만들지 않는다. 결과는 `refine.validate` 를 거쳐 카드에 뜨고, 정본에 쓰는 것은 [제출] 때
    서버가 같은 validator 를 다시 돌린 뒤 한다.
    """
    import urllib.request
    targets = root.get("targets") or []
    pool = root.get("close_pool") or []
    lines = []
    # 글 요점 위에서 한 대화면 **가리키는 말의 뜻**을 먼저 못박는다 (요점 본문은 pool 에 있다).
    if any(str(c.get("source") or "").startswith("글 요점") for c in pool):
        lines.append("0. 이 대화는 글의 요점 목록 위에서 했다 — 내가 'N번'·'요점 N'이라 하면 "
                     "근거 후보의 '요점 N'이다.")
    for i, r in enumerate(thread, 1):
        lines.append("%d. 내가 친 말: %s" % (i, (r.get("ran_ask") or r.get("ask") or "(레인 기본 작업)").strip()))
        fm = r.get("for_me") or {}
        # 피드 첫 턴은 for_me 가 비어 있다 — result 까지 봐야 Claude 줄이 대화에 남는다.
        gist = fm.get("answer") or (fm.get("question") or {}).get("q") or r.get("result") or ""
        if str(gist).strip():
            lines.append("   Claude: %s" % " ".join(str(gist).split())[:300])
        for x in (r.get("answers") or []):
            if isinstance(x, dict) and x.get("a") and not x.get("final"):
                lines.append("   내가 친 말: %s" % x["a"].strip())
    if text:
        lines.append("%d. 내가 친 말(완료): %s" % (len(thread) + 1, text))
    if not any(c.get("kind") == "turn" for c in pool):   # 서랍 초안 — 대화 없이 내 기록만으로 쓴다 (설계 ②)
        lines = ["대화 없음 — 이 항목의 내 기록(근거 후보 record)만으로 쓴다. 지금 그 칸에 적힌 AI 문장을 "
                 "내 기록에 있는 판단으로 **바꿔 쓰는 것**이 이번 일이다. 목적지마다 dests 하나를 낸다. "
                 "record 근거가 없는 목적지만 비운다(빈 것이 낫다). 경위(why)는 2문장·각 160자 이내로 짧게."]
    grounded = grounded_block([], text=(root.get("title") or "") + "\n" + (root.get("question") or ""),
                              code=(root.get("code") or None), slot=(root.get("slot") or None),
                              thread_id=root.get("ts"), budget=3000,
                              targets=[root.get("id"), root.get("path")])
    def fail(why):
        patch(root["ts"], closing_at=None, close_error=why)
        return 3
    with worker_status.Beat("close-%s" % root["ts"], "close", root.get("title", ""), model=model) as beat:
        beat.phase("대화를 정본 문장으로 정리하는 중")
        prompt = refine.prompt("\n".join(lines), pool, targets, grounded=grounded)
        out = worker_claude.run(prompt, beat, [], model=model, timeout=240, max_turns=3, effort="low")
        o = worker_claude.extract_json(out)
        ctx = {"pool": pool, "targets": targets, "root": P}
        ok, rejected, warnings = refine.validate(o, ctx) if isinstance(o, dict) else ([], [], [])
        if not ok and (not isinstance(o, dict) or rejected):
            # 형식·근거 위반은 **한 번만** 고쳐 받는다. 옛 형식으로 조용히 떨어뜨리지 않는다.
            why = "; ".join(r.get("reason", "") for r in rejected)[:300] or "답에 JSON 이 없다"
            beat.log("반려 — " + why)
            fixed = worker_claude.repair_json(
                worker_claude.LAST_SID, model, beat,
                '{"version": 2, "dests": [{"target_id": 후보 id, "text": 정본 문장(끝마다 (t1) 처럼 근거 id), '
                '"why": 경위 2~3문장(같은 표기), "evidence": [{"id","kind","quote","source"}]}]} — ' + why)
            o2 = worker_claude.extract_json(fixed)
            if isinstance(o2, dict):
                ok2, rejected2, warnings2 = refine.validate(o2, ctx)
                if ok2:
                    ok, rejected, warnings = ok2, rejected2, warnings2
        payload = {"version": refine.VERSION, "dests": ok, "rejected": rejected, "warnings": warnings}
        beat.phase("카드에 정리안 띄우는 중" if ok else "반려 사유 남기는 중")
        req = urllib.request.Request(
            "http://127.0.0.1:%s/api/close_final" % os.environ.get("BOARD_PORT", "8787"),
            data=json.dumps({"ts": root["ts"], "text": text, "refine": payload}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                j = json.loads(resp.read().decode() or "{}")
        except Exception as error:   # noqa: BLE001 — 서버가 죽었으면 카드에 남기고 사용자가 다시 누른다
            body = getattr(error, "read", lambda: b"")()
            try:
                j = json.loads(body.decode() or "{}")
            except (ValueError, AttributeError):
                j = {"error": str(error)}
        if j.get("error"):
            beat.finish(False, j["error"])
            return fail(j["error"])
        beat.finish(True, j.get("dest") or ("정리 %d건" % len(ok)))
        print("정리: %d건 (반려 %d)" % (len(ok), len(rejected)))
        return 0


def close_thread(a, text):
    """[완료] 마무리 턴 — 대화를 `refine` 계약으로 정본 문장 + 경위로 가공한다 (설계 ③·④).

    2026-09-17: 옛 마감 둘(사용자 턴 문장 고르기 `close_openq`, 결론 요약 `CLOSE_PROMPT`)은 지웠다.
    목적지 후보(`root.targets`)는 [완료] 때 서버가 발급한다 — 없으면 옛 스레드이므로 다시 누르게 한다.
    화면의 옛 proposal 렌더 분기는 그대로 남아 있다(이미 applied 된 11건).
    """
    rows = store.read()
    by = {r["ts"]: r for r in rows}
    def root_of(r):
        c = r
        while c.get("parent_ts") and by.get(c["parent_ts"]):
            c = by[c["parent_ts"]]
        return c["ts"]
    root = by[root_of(a)]
    thread = sorted([r for r in rows if root_of(r) == root["ts"]], key=lambda r: r["ts"])
    if not root.get("targets"):
        why = "목적지 후보가 없다 — [완료]를 다시 누르면 서버가 발급한다"
        patch(root["ts"], closing_at=None, close_error=why)
        print(why, file=sys.stderr)
        return 3
    return refine_close(root, thread, text, models.resolve(root.get("model"), "card.close"))


def main():
    worker_status.rotate_err_log("run_worker")   # 커졌으면 시작 때 한 번 회전 (.1 하나)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", required=True)
    ap.add_argument("--apply-only", action="store_true",
                    help="이미 나온 결과에서 반영안만 뽑는다 (결과는 건드리지 않음)")
    ap.add_argument("--close", action="store_true", help="[완료] 마무리 턴 — 대화 전체로 첫 물음의 결론을 만들어 정본에 넣는다")
    ap.add_argument("--text", default="", help="--close 때 사용자의 마지막 말")
    args = ap.parse_args()

    a = next((r for r in store.read() if r.get("ts") == args.ts), None)
    if not a:
        print("배정 없음", file=sys.stderr)
        return 1
    if args.close:
        return close_thread(a, (args.text or "").strip())
    src = a.get("source")
    if args.apply_only:
        if journal_writing.enabled(a):
            print("글쓰기는 정본 반영안을 만들지 않는다 — 같은 카드에서 편집한다", file=sys.stderr)
            return 3
        if not a.get("result_body"):
            print("결과가 없어 반영안을 못 만든다", file=sys.stderr)
            return 3
        model = models.resolve(a.get("model"), "card.apply")
        with worker_status.Beat(args.ts, "apply", a.get("title", ""), model=model) as beat:
            beat.phase("정본에서 붙일 자리 찾는 중")
            out = worker_claude.run(APPLY_ONLY_PROMPT.format(
                title=a.get("title", ""), ask=a.get("ran_ask") or a.get("ask") or "(지시 없음)",
                body=a["result_body"][:4000], canon=canon_list(), apply_rule=APPLY_RULE),
                beat, TOOLS, model=model)
            o = worker_claude.extract_json(out) or {}
            cand = o.get("apply")
            ok = (isinstance(cand, dict) and cand.get("file") in APPLY_FILES
                  and isinstance(cand.get("anchor"), str) and cand["anchor"].strip()
                  and isinstance(cand.get("line"), str) and 0 < len(cand["line"].strip()) <= 250)
            if ok:
                patch(args.ts, apply=cand, apply_why=o.get("why"))
                beat.finish(True, "반영안 나옴")
                print("반영안:", cand["file"], cand["anchor"][:40])
                return 0
            why = o.get("why") or worker_claude.failure_text("붙일 자리를 찾지 못함")
            patch(args.ts, apply=None, apply_why=why)
            beat.finish(False, why[:60])
            return 3
    if src in ("map", "openq"):
        if not (a.get("ask") or "").strip():
            print("이 소스는 지시가 있어야 돈다 (버튼 클릭+지시 = 사람 판별)", file=sys.stderr)
            return 3
    elif src != "feed":
        print("feed/map/openq 외 항목 — 무인 실행 안 함 (세션에서 처리)", file=sys.stderr)
        return 3
    if a.get("status") == "in_progress":
        beat = worker_status.read_all().get(args.ts)
        alive = False
        if beat and beat.get("state") == "running" and beat.get("pid"):
            try:
                os.kill(beat["pid"], 0)   # 신호 0 = 생존 확인만
                alive = True
            except (OSError, ProcessLookupError):
                pass
        if alive:
            print("이미 실행 중", file=sys.stderr)
            return 3
        # 이전 워커가 죽었다 (서버 재시작 등) — 다시 돈다.

    lane_name, lane_task = LANE.get(a.get("lane"), ("", ""))
    ask = (a.get("ask") or "").strip()
    ran_ask = ask or f"지시 없음 — 「{lane_name}」 레인 기본 작업으로 실행"
    ask = ask or "(지시 없음 — 레인 기본 작업을 수행하라)"
    prev = a.get("status")
    attempts = int(a.get("attempts") or 0) + 1   # 서버 감시가 3회까지 다시 띄운다 (2026-09-07)
    patch(args.ts, status="in_progress", started_at=datetime.now(KST).isoformat(),
          attempts=attempts, last_error=None)

    model = models.resolve(a.get("model"), "card.answer")   # 카드에서 고른 모델, 없으면 models.json 배정 · 옛 ID 는 현재 ID 로
    followup = False
    writing_mode = journal_writing.enabled(a)
    deep = False   # 글쓰기 분기만 True — effort high·웹 도구
    with worker_status.Beat(args.ts, "run", a.get("title", ""), model=model) as beat:
        beat.phase("지시 읽는 중")
        if src == "openq":
            # 열린 질문 대화 — 묻기만. 결과 for_me.question(+options) 을 서랍이 그린다 (2026-09-10)
            seq = chain(a, store.read())
            followup = bool(seq)
            beat.phase("결정 본문·내 답 읽는 중" if not seq else "앞선 문답 이어 읽는 중")
            out = worker_claude.run(openq_prompt(a, seq), beat, TOOLS, model=model)
        else:
            if src == "map" and writing_mode:
                text = ""  # 대상 폴더 머리 대신 journal 전체 날짜 색인을 쓴다.
            elif src == "map":
                text, err = map_file_text(a)
                if text is None:
                    patch(args.ts, status=prev or "pending")
                    beat.finish(False, err)
                    return 3
            else:
                text = source_text(a.get("id", "")) or a.get("title", "")
            seq = chain(a, store.read())
            followup = bool(seq)
            talk = is_talk(seq[0] if seq else a)
            if talk:
                # 이어서 묻기 — 레인 작업 없음. 첫 턴·후속 같은 프롬프트, 앞선 문답은 history 로 (2026-09-10)
                beat.phase("글 읽고 답하는 중" if not seq else "앞선 문답 이어 읽는 중")
                # 앞선 턴은 한 줄 답만 넘겼다 — 직전 턴은 본문 앞부분까지 넘긴다. 한 줄만으로는
                # 무슨 대화를 하고 있었는지 안 잡힌다 (2026-09-18 사용자 "맥락을 못 잡는다").
                hist = "\n".join(
                    f"{i+1}. 내가 한 말: {(r.get('ask') or '').strip()}\n"
                    f"   나온 답: {(((r.get('for_me') or {}).get('answer')) or r.get('result') or '(결과 없음)').strip()}"
                    + (f"\n   그 답의 본문 앞부분: {(r.get('result_body') or '').strip()[:700]}"
                       if i == len(seq) - 1 and (r.get('result_body') or '').strip() else "")
                    for i, r in enumerate(seq)) or "(첫 턴)"
                troot = seq[0] if seq else a
                prompt = TALK_PROMPT.format(
                    history=hist, ask=ask, canon=canon_list(), text=text[:4000],
                    rule=grounding.RULE,
                    # 후속 대화는 예산을 작게 (4000) — 글 원문·history 가 이미 프롬프트를 채운다
                    grounded=grounded_block(
                        [str((troot.get("brief") or {}).get("scene", {}).get("source", "")).split(" — ")[0]]
                        if ((troot.get("brief") or {}).get("scene") or {}).get("source") else [],
                        text=(troot.get("title") or "") + "\n" + ask,
                        thread_id=troot.get("ts"), budget=4000, exclude_ts=a.get("ts"),
                        targets=[troot.get("id"), troot.get("code"), troot.get("slot")]))
            elif writing_mode:
                deep = True
                beat.phase("일지에서 내 생각 읽는 중" if not seq else "일지와 앞선 글 이어 읽는 중")
                root = seq[0] if seq else a
                ext = ext_sources_block(root, a.get("selected_topic"))
                if a.get("source") != "feed":
                    text = ""            # 지도에서 시작한 글쓰기는 제목이 외부 자료가 아니다
                external = "\n\n".join(x for x in (text, ext) if x)
                if ext:
                    beat.log(f"붙인 보강 자료 {len(root.get('ext_sources') or [])}건 읽음")
                try:
                    prompt = journal_writing.prompt(P, a, seq, external=external,
                                                    free=journal_writing.is_free(root) and not any(
                                                        r.get("refresh_topics") for r in [*seq, a]))
                except (OSError, ValueError, IndexError) as error:
                    why = f"일지·관심 축을 읽을 수 없다: {error}"
                    patch(args.ts, status=prev or "pending", last_error=why)
                    beat.finish(False, why)
                    return 3
            elif seq:
                beat.phase("앞선 대화 읽는 중")
                hist = "\n".join(
                    f"{i+1}. 물음: {(r.get('ask') or '(레인 기본)').strip()}\n"
                    f"   나온 답: {(r.get('result') or '(결과 없음)').strip()}"
                    for i, r in enumerate(seq))
                froot = seq[0]
                prompt = FOLLOWUP_PROMPT.format(
                    root_title=froot.get("title", ""), root_ask=froot.get("ran_ask") or "(지시 없음)",
                    history=hist, ask=ask, canon=canon_list(), text=text[:3000],
                    brief=brief_block(froot), apply_rule=APPLY_RULE,
                    rule=grounding.RULE,
                    grounded=grounded_block(
                        [froot["path"]] if (froot.get("path") and (P / froot["path"]).is_file()) else [],
                        text=(froot.get("title") or "") + "\n" + ask,
                        code=(froot.get("code") or None), slot=(froot.get("slot") or None),
                        thread_id=froot.get("ts"), budget=4000, exclude_ts=a.get("ts"),
                        targets=[froot.get("id"), froot.get("path")]))
            elif src == "map":
                prompt = MAP_PROMPT.format(path=a.get("path", ""), ask=ask,
                                           canon=canon_list(), text=text,
                                           rule=grounding.RULE,
                                           grounded=grounded_block(
                                               [a["path"]] if (P / a.get("path", "")).is_file() else [],
                                               text=(a.get("path") or "") + "\n" + ask + "\n" + text[:2000],
                                               thread_id=a.get("ts"), exclude_ts=a.get("ts"),
                                               targets=[a.get("path")]),
                                           apply_rule=APPLY_RULE)
            else:
                b = (a.get("brief") or {}).get("scene") or {}
                prompt = PROMPT.format(lane_name=lane_name, lane_task=lane_task, ask=ask,
                                       canon=canon_list(), text=text[:4000],
                                       rule=grounding.RULE,
                                       grounded=grounded_block(
                                           [str(b.get("source", "")).split(" — ")[0]] if b.get("source") else [],
                                           text=(a.get("title") or "") + "\n" + ask + "\n" + text[:4000],
                                           thread_id=a.get("ts"), exclude_ts=a.get("ts"),
                                           targets=[a.get("id"), a.get("code"), a.get("slot")]),
                                       brief=brief_block(a), apply_rule=APPLY_RULE)
            if deep:
                # 글쓰기만 깊게 — 생각 high + 공개 수치 확인용 웹 (2026-10-04: 9/14 턴이 WebSearch 권한 거부로
                # 공개 사례 수치를 못 확인했다). 웹 호출이 턴을 먹어 턴·시간 상한도 올린다.
                out = worker_claude.run(prompt, beat, TOOLS + WRITING_WEB, model=model,
                                        effort="high", timeout=900, max_turns=30)
            else:
                out = worker_claude.run(prompt, beat, TOOLS, model=model)
        beat.phase("결과 검증 중")
        o = worker_claude.extract_json(out)
        tight = (src == "map" and not followup)
        root_row = seq[0] if (writing_mode and seq) else a
        def accepted(result):
            if writing_mode:
                # 후속 초안 턴의 글감 후보는 계약상 빈 배열 — 비우고 남긴다 (조용히 바꾸지 않게 로그 한 줄)
                cut = journal_writing.trim_followup(result, followup, refresh=bool(a.get("refresh_topics")))
                if cut:
                    beat.log(f"후속 턴이라 글감 후보 {cut}건 뺌")
            return (valid(result, tight=tight) and (not writing_mode
                    or journal_writing.valid(result, P, first=not followup,
                                             ask=root_row.get("ask") if journal_writing.is_free(root_row) else None)))
        if out and not accepted(o):
            # 형식만 틀린 답은 같은 세션에서 한 번 고쳐 받는다 — 다시 탐색시키지 않는다 (2026-09-07)
            contract = ('{"summary": 한 문장, "body": 80자 이상, "for_me": {...앞서 지시한 대로}, '
                        '"apply": null 또는 {"file","anchor","line"}}'
                        + (" · 길이: summary 60자·for_me.next 50자·apply.line 120자 이내" if tight else ""))
            if src == "feed" and is_talk(seq[0] if seq else a):   # 이어서 묻기: answer 형식, apply null (2026-09-10)
                contract = ('{"summary": 한 문장, "body": 80자 이상, "for_me": {"answer": 답 2~5문장, "so_far": [앞선 턴 한 줄씩, 없으면 []], '
                            '"next": 한 줄}, "apply": null} — question·points 키 금지')
            if src == "openq":   # 열린 질문: question 은 객체 — 평탄화가 실측 불합격 원인 (2026-09-10)
                contract = ('{"summary": 한 문장, "body": 80자 이상, "for_me": {"question": {"q": 물음표로 끝나는 한 문장, '
                            '"quote": 사용자 문장 그대로, "source": "대화" 또는 파일 경로}, "options": [{"label": 40자 이내 구}] (0~3개), '
                            '"next": 한 줄}, "apply": null} — question 을 문자열로 쓰지 말고 quote·source 를 question 안에')
            if writing_mode:
                contract += (" · writing.version:2, draft/question/changes 문자열 필수. draft/revise 단계는 draft에 "
                             "80자 이상의 글 전문, question에는 질문 하나 또는 빈 문자열. support는 id/role/why 최대 2개. ")
                contract += (" · writing:{stage:topics|clarify|draft|revise,topics:[{title,axis,angle,"
                             "sources:[{path,quote}],missing}]} 필수. 첫 턴 topics. 후보 최대 3개, "
                             "관심 축 표의 코드만, journal 날짜 파일에 실제 있는 본인 인용만"
                             + (" (새 글 모드: path 'ask' 로 이번 요청 문장 인용도 됨)" if journal_writing.is_free(root_row) else "") + ". "
                             "없는 인용·AI층 인용은 후보에서 빼고 이유를 body에 쓴다. apply:null.")
            fixed = worker_claude.repair_json(worker_claude.LAST_SID, model, beat, contract)
            o2 = worker_claude.extract_json(fixed)
            if accepted(o2):
                out, o = fixed, o2
        # 길이 상한(tight)은 MAP_PROMPT 가 60·50·120자를 말해 준 첫 턴에만 — 후속(FOLLOWUP_PROMPT)은
        # 대화라서 상한을 안 알렸고, 여기서 상한을 걸면 멀쩡한 답이 통째로 버려진다
        # (2026-09-04 실측: 지도 후속 3건 연속 "결과 형식이 계약과 안 맞는다" — summary 88·next 98·
        # apply.line 188자, 내용은 정상. 모델 문제가 아니라 프롬프트·검증 계약 불일치).
        if not accepted(o):
            # 왜 떨어졌는지 다음에 볼 수 있게 — 마지막 불합격 원문을 남긴다
            try:
                (Path(__file__).parent / ".worker_last_bad.json").write_text(
                    json.dumps({"ts": args.ts, "out": out}, ensure_ascii=False))
            except OSError:
                pass
            # 답 자체가 없던 것(네트워크·로그인·턴 한도)과 형식이 틀린 것을 가른다 — 다음 수가 다르다.
            # 원인 문구는 워커가 항상 남긴다 (열거는 worker_claude.failure_text 한 곳).
            why = worker_claude.failure_text("결과 형식이 계약과 안 맞는다 (한 번 고쳐 받아도 안 맞음)"
                                             if out else "모델이 답을 못 냈다 — [다시]로 재시도")
            patch(args.ts, status=prev or "briefed", last_error=why)
            beat.retry_note = f"곧 다시 해볼게요 ({attempts}/3)" if attempts < 3 else "세 번 다 안 됐어요"
            beat.finish(False, why)
            return 1

        patch(args.ts, status="done", result=o["summary"].strip(),
              result_body=o["body"].strip(), for_me=o.get("for_me"),
              apply=(o.get("apply") if src in ("feed", "map") and not is_talk(seq[0] if seq else a) else None), outputs=[],
              ran_ask=ran_ask, ran_model=model, seen_at=None,
              writing=o.get("writing") if writing_mode else None,
              done_at=datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z"))
        beat.finish(True, "완료")
        print(f"완료: {o['summary'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
