"""제품 폴더와 사용자 데이터 폴더를 가른다.

제품 폴더(이 패키지)는 업데이트 때 통째로 바뀐다. 사용자 데이터는 따로 둔 데이터 폴더에만 쓴다.
데이터 폴더 위치는 --data 인자 > CRM_DATA 환경변수 > 제품 폴더의 local.json 순서로 찾는다.
local.json 은 이 컴퓨터 전용 파일이라 배포 패키지에 넣지 않는다.
"""
import json
import os
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONF = PRODUCT_ROOT / "local.json"
TEMPLATES = PRODUCT_ROOT / "templates"
SAMPLE_DATA = PRODUCT_ROOT / "sample-data"
WEB = PRODUCT_ROOT / "web"

# 데이터 폴더 안의 고정 이름. 표시 이름이 아니라 내부 경로라 바꾸지 않는다.
SETTINGS = ("meta.json", "profile.json", "questions.json", "sources.json",
            "tabs.json", "candidates.json", "setup-state.json")
USER_DIRS = ("journal", "sheets")
SYS_DIR = ".crm"            # 실행 상태·백업·잠금. 정본이 아니다.
OLD_SYS_DIR = ".canon"      # 이름 변경 전 시스템 폴더 — migrate_sys_dir 만 쓴다. 옛 이름 문자열은 이 한 곳에만 둔다.

# env 이름: 새 이름 CRM_* 를 먼저, 없으면 옛 이름(이름 변경 호환). 옛 이름을 쓰면 이름마다 1줄 알림(stderr).
ENV_NEW, ENV_OLD = "CRM_", "CANON_"   # 옛 이름 문자열은 이 한 곳에만 둔다.
_env_warned = set()


def env(name, default=None):
    v = os.environ.get(ENV_NEW + name)
    if v not in (None, ""):
        return v
    old = os.environ.get(ENV_OLD + name)
    if old is not None:
        if name not in _env_warned:
            _env_warned.add(name)
            print(f"옛 env 이름 {ENV_OLD}{name} — {ENV_NEW}{name} 로 옮기세요", file=sys.stderr, flush=True)
        return old
    return v if v is not None else default


def env_names(name):
    """새·옛 이름 둘 다 — 시험이 env 를 지울 때 둘 다 지우게."""
    return ENV_NEW + name, ENV_OLD + name


def read_local_conf():
    try:
        return json.loads(LOCAL_CONF.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def resolve_data_root(explicit=None):
    """찾은 데이터 폴더 경로(Path)나 None. 존재 여부는 보지 않는다."""
    raw = explicit or env("DATA") or read_local_conf().get("data_root")
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def sys_dir(data):
    return Path(data) / SYS_DIR


def migrate_sys_dir(data):
    """이름 변경 호환: 데이터 폴더에 새 시스템 폴더가 없고 옛 폴더만 있으면 한 번 옮긴다(백업·이력 그대로).
    옮겼으면 True. 실패(OSError)는 부르는 쪽이 오류로 멈춘다 — 새 폴더를 따로 만들면 옛 백업이 안 보인다."""
    old = Path(data) / OLD_SYS_DIR
    new = Path(data) / SYS_DIR
    if new.exists() or not old.is_dir():
        return False
    os.rename(old, new)
    print(f"시스템 폴더를 옮겼어요: {old.name} → {new.name} (백업·이력 그대로)", file=sys.stderr, flush=True)
    return True


def mask_path(text, data=None):
    """진단·로그에 실을 문자열에서 홈·데이터 경로를 가린다."""
    s = str(text)
    if data:
        s = s.replace(str(Path(data)), "<DATA>")
    s = s.replace(str(PRODUCT_ROOT), "<PRODUCT>")
    s = s.replace(str(Path.home()), "~")
    return s
