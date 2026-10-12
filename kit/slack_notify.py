#!/usr/bin/env python3
"""Slack 알림 — 봇 명의 게시 (모델 없음, 결정적). 설정이 없으면 아무것도 보내지 않는다.

켜는 법 (둘 다 선택):
  - CANON_SLACK_CHANNEL = 게시할 채널 ID. **없으면 알림 끔** (봇 경로 전체를 건너뛴다).
  - 봇 토큰 파일: SLACK_TOKEN_FILE (기본 ~/.config/personal-canon-kit/slack-token, xoxb-, 0600)
  - 예비: <CANON_ROOT>/.env 또는 환경변수의 SLACK_WEBHOOK (Incoming Webhook)
사용: python3 kit/slack_notify.py "한 줄"   |   python3 kit/slack_notify.py --file brief.md
exit: 0=게시 · 2=설정 없음 · 1=전송 실패
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)

HOME = os.path.expanduser("~")
ENV = str(paths.root() / ".env")
KEY = "SLACK_WEBHOOK"
TOKEN_FILE = os.environ.get("SLACK_TOKEN_FILE") or os.path.join(HOME, ".config", "personal-canon-kit", "slack-token")
CHANNEL = os.environ.get("CANON_SLACK_CHANNEL") or ""   # 비었으면 알림 끔


def bot_token():
    if not CHANNEL:
        return None
    try:
        st = os.stat(TOKEN_FILE)
    except FileNotFoundError:
        return None
    if st.st_mode & 0o077:
        print(f"token_file 권한 느슨({oct(st.st_mode & 0o777)}) — chmod 600 필요, 봇 경로 건너뜀", file=sys.stderr)
        return None
    tok = open(TOKEN_FILE).read().strip()
    return tok if tok.startswith("xoxb-") else None


def post_as_bot(tok, text):
    req = urllib.request.Request("https://slack.com/api/chat.postMessage",
                                 data=json.dumps({"channel": CHANNEL, "text": text, "unfurl_links": False,
                                                  "unfurl_media": False}).encode(),
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    if not d.get("ok"):
        raise RuntimeError(d.get("error", "unknown"))
    return d.get("ts")


def webhook():
    if os.environ.get(KEY):
        return os.environ[KEY]
    try:
        for line in open(ENV):
            line = line.strip()
            if line.startswith(KEY + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def main():
    args = sys.argv[1:]
    # 옵션처럼 보이는 인자는 메시지로 보내지 않는다 — 2026-09-04 헤드리스 판단 세션이 `--help` 를
    # 실행해 "--help" 문자열이 채널에 게시된 사고 (즉시 삭제). 유효 옵션은 --file 하나.
    if any(a.startswith("-") for a in args) and args[:1] != ["--file"]:
        print(__doc__.strip(), file=sys.stderr); return 1
    if args[:1] == ["--file"]:
        try:
            text = open(args[1]).read()
        except OSError as e:
            print(f"파일 없음: {e}", file=sys.stderr); return 1
    elif args:
        text = " ".join(args)
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        print("빈 메시지 — 전송 안 함", file=sys.stderr); return 1
    text = text[:39000]
    if not CHANNEL and not webhook():
        print("CANON_SLACK_CHANNEL·webhook 없음 — 알림 끔", file=sys.stderr); return 2
    tok = bot_token()
    if tok:
        try:
            ts = post_as_bot(tok, text)
            print(f"봇 게시 완료 (ts={ts})"); return 0
        except Exception as e:
            # not_in_channel = 봇을 채널에 초대 안 함 → 채널에서 /invite. 실패 시 webhook 으로 계속.
            print(f"봇 게시 실패: {e} — webhook 경로 시도", file=sys.stderr)
    url = webhook()
    if not url:
        print("봇 토큰·webhook 둘 다 없음 — 알림 끔", file=sys.stderr); return 2
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
    except Exception as e:
        print(f"webhook 실패: {e}", file=sys.stderr); return 1
    if body.strip() != "ok":
        print(f"webhook 응답 이상: {body[:200]}", file=sys.stderr); return 1
    print("봇 게시 완료"); return 0


if __name__ == "__main__":
    sys.exit(main())
