"""관리자 복구·계정 삭제 CLI (β2 · PR B) — 웹 서버 없이 DB 에 바로 붙는다. 원작자 Mac 에서만, DATABASE_URL 을 명시해 돌린다.

    DATABASE_URL=… .venv/bin/python admin_cli.py grant <email>   # 그 계정을 관리자로 + 차단 해제 + 감사 recover
    DATABASE_URL=… .venv/bin/python admin_cli.py list            # 관리자 목록(차단 여부 포함)

    # 계정 삭제(PR B) — 대상 = 「개인정보 요청」 문의(kind=privacy)의 소유자. 이메일로 대상을 고르지 않는다. --actor = 실행하는 관리자 user id
    DATABASE_URL=… .venv/bin/python admin_cli.py delete-notify  <ticket_id> --actor <uid>          # 통지(문의 공개 답변) → 7일 유예 시작
    DATABASE_URL=… .venv/bin/python admin_cli.py delete-preview <ticket_id> --actor <uid>          # 읽기 전용 — 건수·실행 가능 여부·미리보기 값
    DATABASE_URL=… .venv/bin/python admin_cli.py delete-run     <ticket_id> <미리보기 값> --actor <uid>   # 실행(한 트랜잭션)
    DATABASE_URL=… .venv/bin/python admin_cli.py delete-cancel  <ticket_id> --actor <uid>          # 유예 중 취소(통지 행 삭제)

.env 는 읽지 않는다 — 어느 DB 에 붙는지 명령 줄에서 보이게. 이메일은 소문자로 맞춘다. 대상은 먼저 한 번 로그인해 계정이 있어야 한다.
삭제 명령의 출력은 건수·시각(KST)·코드만이다 — 이메일·표시 이름·본문·별칭·출처 값·답변을 찍지 않는다. 오류 = `[코드] 문구`, 종료 1.
"""
import sys

import db

USAGE = ("쓰는 법: admin_cli.py grant <email> | admin_cli.py list\n"
         "        admin_cli.py delete-notify|delete-preview|delete-cancel <ticket_id> --actor <uid>\n"
         "        admin_cli.py delete-run <ticket_id> <미리보기 값> --actor <uid>")
DELETE_ARGS = {"delete-notify": 1, "delete-preview": 1, "delete-run": 2, "delete-cancel": 1}   # 명령 → 위치 인자 수(--actor 빼고)


def _kst(v) -> str:
    return v.astimezone(db.KST).strftime("%Y-%m-%d %H:%M KST") if v is not None else "-"


def _delete(cmd: str, pos: list[str], actor: str) -> None:
    if cmd == "delete-notify":
        r = db.delete_notify(actor, pos[0])
        print(f"통지했어요: 문의 {r['ticket_id']} · 통지 {_kst(r['notified_at'])} · 실행 가능 {_kst(r['delete_after'])} 이후")
        print(f"접수 뒤 10일 안 통지 = {'예' if r['within_10_days'] else '아니오'}")
    elif cmd == "delete-preview":
        r = db.delete_preview(actor, pos[0])
        for t, n in r["counts"].items():
            print(f"  지움\t{t}\t{n}")
        print(f"지울 행 합계 {r['total']} · 보존: 문의 {r['keep']['tickets']} · 이력 {r['keep']['ticket_history']} · contact {r['keep']['deletion_contacts']}")
        print(f"통지 {_kst(r['notified_at'])} · 실행 가능 {_kst(r['delete_after'])} 이후")
        print(f"실행 가능 = {'예' if r['ready'] else '아니오'}" + (f" (사유: {', '.join(r['reasons'])})" if r["reasons"] else ""))
        print(f"미리보기 값 {r['value']}")
    elif cmd == "delete-run":
        r = db.delete_run(actor, pos[0], pos[1])
        print(f"삭제했어요: 문의 {r['ticket_id']} · 지운 행 {r['deleted']} · 실행 {_kst(r['deleted_at'])}")
    else:
        r = db.delete_cancel(actor, pos[0])
        print(f"취소했어요: 문의 {r['ticket_id']} — 사용자 답변은 /admin 문의 화면에서 쓰세요")


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd in DELETE_ARGS:
        rest = argv[1:]
        if "--actor" not in rest or rest.index("--actor") + 1 >= len(rest):
            print(USAGE, file=sys.stderr)
            return 2
        i = rest.index("--actor")
        actor, pos = rest[i + 1], rest[:i] + rest[i + 2:]
        if len(pos) != DELETE_ARGS[cmd]:
            print(USAGE, file=sys.stderr)
            return 2
    elif not argv or cmd not in ("grant", "list") or (cmd == "grant" and len(argv) != 2) or (cmd == "list" and len(argv) != 1):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if cmd in DELETE_ARGS:
            _delete(cmd, pos, actor)
        elif cmd == "grant":
            r = db.admin_recover(argv[1])
            print(f"관리자로 지정했어요(차단 해제·감사 recover): {r['email']} ({r['id']})")
        else:
            rows = db.admins()
            for r in rows:
                print(f"{r['id']}\t{r['email']}\t{'차단 ' + r['blocked_at'] if r['blocked_at'] else ''}".rstrip())
            print(f"관리자 {len(rows)}명")
    except db.CrmError as e:
        print(f"[{e.code}] {e.message}" + (f" {e.action}" if e.action else ""), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
