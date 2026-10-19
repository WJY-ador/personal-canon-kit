# CHANGELOG

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
