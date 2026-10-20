# CHANGELOG

## 로컬 에이전트·연결 장치 — 2026-10-DD

호스팅 판에 내 PC 를 「연결 장치」로 붙이는 로컬 에이전트 `agent/` 를 더했다. 원문은 PC, 서버엔 구조화 정보만.

- `agent/`: `python3 -m agent` — connect·validate·run-once·status·schedule install/remove·serve·disconnect. 표준 라이브러리만(Python ≥3.9)
- 첫 입력원 = 좋아요 `likes.jsonl` → `like` 활동. 행동 날짜는 비우고(「날짜 미확인」) 수집기가 처음 본 날만 따로 보낸다. 다른 어댑터는 골격만(꺼짐)
- macOS: 토큰·HMAC 키는 Keychain, 예약은 launchd 1시간. Windows 는 자격 증명 저장소 미구현 — 계약 검증·원문 보기만
- 원문 보기는 `127.0.0.1` 전용(기본 포트 8790), 확인 화면 → 일회용 토큰 → 원문
- 무인 요약(구독 CLI)은 기본 꺼짐 — 이용 조건 확인 전에는 켜지 않는다. 직접 돌린 `run-once` 는 attended, 예약 실행은 unattended 로 신고
- 웹에서 허용 규칙을 바꾸면 에이전트가 다음 업로드 때 새 규칙을 자동으로 다시 받아 한 번 재시도(토큰 재입력 없음)
- `agent/board_manifest.py`: board 정본 → AI 검토표·이전 명세(`board-migration/1`). 참조는 정확한 판(`{original_id, revision}`). 고객사 폴더는 설정 `forbidden_roots` 로만 정한다(코드 기본값 없음)
- 문서: 처리방침 v6(연결 장치·활동·승인 대기·PC 원문) · 가이드 09·10 갱신 · `agent/README.md`
- manifest: `agent/` 파일 등록(선택), 지원표 「수집 없음」 갱신

## 옛 이름 호환 제거 — 2026-10-19

옛 이름 호환 제거 — CANON_* env 와 .canon 폴더는 더 읽지 않는다.

- env 는 `CRM_*` 만 읽는다. 옛 이름 알림 줄도 없앴다
- 데이터 폴더의 시스템 폴더는 `.crm/` 만 쓴다. 첫 실행 때 옮기던 단계를 없앴다

## 이름 변경 — 2026-10-19

이름 변경: canon → personal-crm / My CRM. 기능은 그대로다.

- 보이는 이름 My CRM · 식별자 `personal-crm` · MCP 서버 이름 `my-crm`
- 명령 `canon.py` → `crm.py`, 코드 `canonkit/` → `crmkit/`, 스킬 `canon-*` → `crm-*`
- env `CANON_*` → `CRM_*`. 옛 이름도 당분간 읽는다 — 쓰면 옛 이름마다 「옮기세요」 알림 한 줄
- 데이터 폴더의 시스템 폴더 `.canon/` → `.crm/`. 첫 실행 때 한 번 옮긴다(백업·이력 그대로, 알림 한 줄)
- 화면 테마 기억(localStorage) 키가 바뀌어 한 번 초기화된다

## 0.1.0 — 2026-10-15

첫 판. schemaVersion 1.

- 명령: init·doctor·diag·status·setup·consent·profile·request·question·source·blocks·tabs·record·journal·digest·candidates·candidate·sheet·serve·backup·backups·restore·drill·issue·next·check
- 읽기 전용 대시보드: 설정 기반 탭(최대 3개), 블록 6종(질문별 한 장·확인 대기 판단·최근 기록·내 질문·다음 행동·실행 상태)
- 양식 9종, 가이드 8개, 스킬 4개(canon-setup·canon-record·canon-digest·canon-doctor), 가짜 샘플 데이터
- 호환: 첫 판이라 마이그레이션 없음
- 지원표: macOS 시험함 / Windows 네이티브·WSL 미검증
