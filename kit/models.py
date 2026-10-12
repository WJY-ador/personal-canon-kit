"""Claude 모델 ID 한 자리 — scripts/models.json 을 읽는다 (Python 3.9 호환).

호출 지점은 ID 를 직접 쓰지 않고 `use("<호출 키>")` 로 받는다. 등급 배정은 models.json 의
`uses` 한 곳이다. 카드·후속처럼 옛 ID 가 jsonl 에 남아 있는 값은 읽는 시점에 `resolve()` 로
현재 ID 로 바꾼다 (jsonl 은 다시 쓰지 않는다).

셸: python3 scripts/models.py <호출 키 | 등급>   → ID 한 줄 (모르는 키면 exit 2)
"""
import json
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().with_name("models.json")


def _load():
    # 매번 읽는다 — 서버는 오래 떠 있어서 models.json 만 바꿔도 다음 호출부터 따라가게.
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def tier_id(tier):
    return _load()["ids"][tier]


def default():
    d = _load()
    return d["ids"][d["default"]]


def use(key):
    d = _load()
    return d["ids"][d["uses"][key]]


def resolve(model, key=None):
    """카드·인자로 들어온 모델 값 → 현재 ID. 비었으면 key 배정(없으면 기본), 옛 ID·등급명은 바꾼다."""
    d = _load()
    if not model:
        return d["ids"][d["uses"][key]] if key else d["ids"][d["default"]]
    if model in d["ids"]:                       # "opus" 같은 등급명
        return d["ids"][model]
    tier = d["legacy"].get(model)
    return d["ids"][tier] if tier else model


def client_config():
    """화면(보드·지도)에 싣는 몫 — 선택 목록·기본값·옛 ID 매핑."""
    d = _load()
    ids = d["ids"]
    return {"default": ids[d["default"]],
            "map_default": ids[d["uses"]["map.card"]],
            "models": [{"id": ids[u["tier"]], "name": u["name"]} for u in d["ui"]],
            "map_models": [{"id": ids[u["tier"]], "name": u["name"]} for u in d["map_ui"]],
            "legacy": {old: ids[t] for old, t in d["legacy"].items()}}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: models.py <호출 키 | 등급>", file=sys.stderr)
        sys.exit(2)
    _d = _load()
    _k = sys.argv[1]
    if _k in _d["uses"]:
        print(use(_k))
    elif _k in _d["ids"]:
        print(_d["ids"][_k])
    else:
        print("모르는 모델 키: %s" % _k, file=sys.stderr)
        sys.exit(2)
