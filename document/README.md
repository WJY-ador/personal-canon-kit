---
id: help-index
title: Help_문서
status: draft
visibility: public
---

# Help_문서

My CRM 문서를 찾는 통합 목차입니다. 사용 중인 판과 각 문서의 지원 조건을 먼저 확인하세요. 이 목차의 새 규격 문서는 검토 중이며, 규격에 적힌 기능이 모두 구현됐다는 뜻은 아닙니다.

## 사용 목적별로 찾기

| 목적 | 현재 정본 | 적용 범위 |
|---|---|---|
| 제품과 시작 순서 이해 | [README](../README.md), [START](../START.md), [구매 전 확인](../guides/01-before-you-buy.md) | 로컬 키트와 호스팅 전환 설명 구분 |
| 호스팅 서비스 연결·사용 | [호스팅 시작](../guides/09-hosted-start.md), [화면 용어](../guides/10-words.md) | 호스팅 판 |
| 로컬 키트 설치 | [macOS](../guides/02-install-macos.md), [Windows](../guides/03-install-windows.md) | 로컬 키트 설치 문서이며 호스팅 에이전트의 두 OS 지원 보장이 아님 |
| 설정·일상 사용 | [설정 대화](../guides/04-setup-conversation.md), [매일 쓰기](../guides/05-daily-use.md) | 각 문서의 판·조건 확인 |
| 자료 연결 | [입력원 연결](../guides/06-connect-sources.md), [로컬 에이전트](../agent/README.md) | 수집원·OS별 제약 확인 |
| 문제 해결·이동 | [점검과 복구](../guides/07-check-and-recover.md), [업데이트·이동·제거](../guides/08-update-move-remove.md) | 로컬 키트 |
| 개인정보 처리 확인 | [개인정보 처리방침](../guides/11-privacy.md) | 게시된 방침의 시행일·범위 확인 |

## 작성자·개발자·운영자

| 목적 | 정본 |
|---|---|
| 문서 작성·명명·검토 | [문서 규격](standard.md) |
| 공통 리스트·상세 화면 계약 | [페이지 레이아웃](reference/page-layout.md) |
| 데이터 구조 확인 | [데이터 모델 설명(실행 스키마와 대조)](../server/data-model.md), [실행 스키마](../server/schema.sql) |
| 서버 실행·운영 | [서버 README](../server/README.md) |

## 점진 통합 범위

새 문서는 `document/`를 정본으로 사용합니다. 기존 `guides/` 본문은 이곳에 복사하지 않습니다. 현재 웹 Help·MCP 문서 로더, 처리방침 기동 검사, 링크 매핑과 패키지 manifest가 기존 경로를 사용하므로 이번 문서 작업에서는 이동하지 않습니다.

다음은 이전할 때의 분류 기준입니다. 목적별 내용을 대조해 한 주제의 정본을 결정한 뒤 경로·로더·링크·manifest를 함께 전환합니다. 아래 경로가 모두 존재하거나 해당 기능이 지원된다는 뜻은 아닙니다.

| 현재 문서 | 향후 분류 |
|---|---|
| `guides/01`~`04`, `09` | `help/start/` — 시작과 연결 |
| `guides/05` | `help/write/` — 기록·수정·재사용 |
| `guides/06`, 에이전트의 수집 안내 | `help/collect/` — 사용자가 선택한 자료 수집 |
| Contact 사용 안내 | `help/people/` — 사람과 연결 자료 열람 |
| 검색·저장된 보기 안내 | `help/find/` — 필요한 기록 찾기 |
| `guides/07`~`08`의 사용자 조치 | `help/troubleshoot/` — 복구·이동 |
| `guides/10`, 오브젝트·필드·관계 설명 | `reference/` — 계약·용어 |
| 공개 가능한 일반 운영 절차 | `operations/` — 비밀을 포함하지 않는 운영 |
| `guides/11-privacy.md` | 전용 방침 경로 유지. 일반 Help 이전과 별도로 로더·시행일 검증 |

수집 설정과 결과 열람은 다른 목적입니다. 예를 들어 선택한 카톡방 요약 안내는 `help/collect/`, 대화한 사람을 살펴보는 안내는 `help/people/`에 둡니다. 실제 내용이 준비될 때 문서를 만들며 빈 폴더·자리표시 문서는 먼저 만들지 않습니다.

이 파일이 `document/`의 유일한 통합 목차입니다. 다른 위치에서는 목차를 복제하지 않고 이 파일을 연결합니다.
