"""제품 폴더와 사용자 데이터 폴더를 가른다.

제품 폴더(이 패키지)는 업데이트 때 통째로 바뀐다. 사용자 데이터는 따로 둔 데이터 폴더에만 쓴다.
데이터 폴더 위치는 --data 인자 > CANON_DATA 환경변수 > 제품 폴더의 local.json 순서로 찾는다.
local.json 은 이 컴퓨터 전용 파일이라 배포 패키지에 넣지 않는다.
"""
import json
import os
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
SYS_DIR = ".canon"          # 실행 상태·백업·잠금. 정본이 아니다.


def read_local_conf():
    try:
        return json.loads(LOCAL_CONF.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def resolve_data_root(explicit=None):
    """찾은 데이터 폴더 경로(Path)나 None. 존재 여부는 보지 않는다."""
    raw = explicit or os.environ.get("CANON_DATA") or read_local_conf().get("data_root")
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def sys_dir(data):
    return Path(data) / SYS_DIR


def mask_path(text, data=None):
    """진단·로그에 실을 문자열에서 홈·데이터 경로를 가린다."""
    s = str(text)
    if data:
        s = s.replace(str(Path(data)), "<DATA>")
    s = s.replace(str(PRODUCT_ROOT), "<PRODUCT>")
    s = s.replace(str(Path.home()), "~")
    return s
