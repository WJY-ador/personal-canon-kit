"""관리자 복구 CLI (β2) — 웹 서버 없이 DB 에 바로 붙는다. 원작자 Mac 에서만, DATABASE_URL 을 명시해 돌린다.

    DATABASE_URL=… .venv/bin/python admin_cli.py grant <email>   # 그 계정을 관리자로 + 차단 해제 + 감사 recover
    DATABASE_URL=… .venv/bin/python admin_cli.py list            # 관리자 목록(차단 여부 포함)

.env 는 읽지 않는다 — 어느 DB 에 붙는지 명령 줄에서 보이게. 이메일은 소문자로 맞춘다. 대상은 먼저 한 번 로그인해 계정이 있어야 한다.
"""
import sys

import db

USAGE = "쓰는 법: admin_cli.py grant <email> | admin_cli.py list"


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("grant", "list") or (argv[0] == "grant" and len(argv) != 2) or (argv[0] == "list" and len(argv) != 1):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if argv[0] == "grant":
            r = db.admin_recover(argv[1])
            print(f"관리자로 지정했어요(차단 해제·감사 recover): {r['email']} ({r['id']})")
        else:
            rows = db.admins()
            for r in rows:
                print(f"{r['id']}\t{r['email']}\t{'차단 ' + r['blocked_at'] if r['blocked_at'] else ''}".rstrip())
            print(f"관리자 {len(rows)}명")
    except db.CanonError as e:
        print(f"[{e.code}] {e.message}" + (f" {e.action}" if e.action else ""), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
