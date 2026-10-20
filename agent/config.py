"""에이전트 홈(레포 밖 사용자 디렉터리)과 설정 파일.

홈 위치: --home 인자 > MYCRM_AGENT_HOME 환경변수 > OS 기본
  macOS   ~/Library/Application Support/my-crm-agent
  Windows %APPDATA%\\my-crm-agent
  기타    ~/.local/share/my-crm-agent
홈 안: config.json(0600) · state.sqlite(0600) · raw/ · pending/ · locks/ · logs/ (전부 0700)

설정엔 토큰·HMAC 키를 넣지 않는다. Keychain 항목 이름만 둔다. 입력 경로는 전부 설정에서 온다.
"""
from __future__ import annotations

import copy
import json
import os
import re
from urllib.parse import urlsplit

from . import contract, osutil

CONFIG_VERSION = 1
APP_DIR = "my-crm-agent"
KEYCHAIN_SERVICE = "my-crm-agent"
DEFAULT_VIEWER_PORT = 8790   # board-dev 8787 회피. 웹 화면도 이 값을 기본으로 쓴다

# 활성 구현 어댑터 / 의미 규칙만 있는 골격(기본 비활성) / 전송 분기 자체 차단
ACTIVE_ADAPTERS = ("likes",)
SKELETON_ADAPTERS = ("feed", "reactions", "mail", "kakao")
BLOCKED_ADAPTERS = ("linkedin_moves",)

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# 키 공간 ID — 요청엔 안 들어가고 Keychain 항목 이름에만 쓴다. 서버 표시 형식이 미정이라 안전 문자만 본다
NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{6,64}$")


class ConfigError(ValueError):
    pass


def default_home() -> str:
    env = os.environ.get("MYCRM_AGENT_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if osutil.IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_DIR)
    if osutil.IS_MAC:
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_DIR)
    return os.path.join(os.path.expanduser("~"), ".local", "share", APP_DIR)


class Paths:
    def __init__(self, home: str):
        self.home = os.path.abspath(home)
        self.config = os.path.join(self.home, "config.json")
        self.db = os.path.join(self.home, "state.sqlite")
        self.raw = os.path.join(self.home, "raw")
        self.pending = os.path.join(self.home, "pending")
        self.locks = os.path.join(self.home, "locks")
        self.logs = os.path.join(self.home, "logs")
        self.summary_work = os.path.join(self.home, "summary-work")

    def ensure(self) -> "Paths":
        for d in (self.home, self.raw, self.pending, self.locks, self.logs, self.summary_work):
            osutil.ensure_private_dir(d)
        return self

    def lock(self, name: str) -> str:
        if not re.match(r"^[a-z0-9_-]{1,40}$", name):
            raise ConfigError("lock name")
        return os.path.join(self.locks, name + ".lock")


def default_config() -> dict:
    return {
        "config_version": CONFIG_VERSION,
        "server_url": None,
        "source_instance_id": None,
        "device_label": None,
        "keychain_service": KEYCHAIN_SERVICE,
        "hmac_namespace": None,
        "policy_revision": 1,
        "allowed_axes": [],
        "allowed_actions": None,             # 서버 policy.actions (None = 아직 모름 → 서버가 판정)
        "allowed_transforms": None,          # 서버 policy.transform_versions
        "token_expires_at": None,            # 표시용(서버 GET /api/imports/device)
        "viewer_port": DEFAULT_VIEWER_PORT,
        "upload": {"max_per_run": 100, "min_interval_sec": 6.5, "timeout_sec": 30},
        "adapters": {
            "likes": {
                "enabled": False,
                "input_path": None,          # 수집기 likes.jsonl 위치 — 사용자가 채운다
                "provider_code": None,       # HMAC 제공자 코드(예: 플랫폼 이름 소문자)
                "include_kinds": [],         # 보낼 kind 값들. ["*"] = null 아닌 전부. 비면 아무 행도 안 보냄
                "include_null_kind": False,
                "include_seed": False,
                "axes": [],                  # 고정 축 매핑(allowed_axes 안)
            },
            "feed": {"enabled": False},
            "reactions": {"enabled": False},
            "mail": {"enabled": False},
            "kakao": {"enabled": False},
        },
        "summary": {
            "enabled": False,                # 약관 확인 전 끔
            "input_dir": None,               # 사람이 지정·확인한 자기 기록 전용 폴더
            "cli_path": None,                # 구독 CLI 절대 경로
            "tool_code": "claude-cli",
            "model_code": "unknown",
            "timeout_sec": 300,
            "daily_attempts": 10,
            "file_glob_suffix": ".md",
        },
    }


# likes 설정 예시(원작자가 설정 파일을 만들 때 쓰는 값, 2026-10-20 메인 확정).
# 수집기 파일 전체가 본인 좋아요 페이지 산출이고, kind 는 사람이 누른 분류 태그(좋아요 여부와 무관),
# seed=true 는 첫 실행분 표시다 → 전부 보낸다. 코드 기본값(아무 행도 안 보냄)은 바꾸지 않는다.
LIKES_EXAMPLE = {
    "enabled": True,
    "input_path": "/absolute/path/to/likes.jsonl",
    "provider_code": "threads",
    "include_kinds": ["*"],
    "include_null_kind": True,
    "include_seed": True,
    "axes": [],
}


def example_config() -> dict:
    cfg = default_config()
    cfg["adapters"]["likes"] = dict(LIKES_EXAMPLE)
    return cfg


def token_account(source_instance_id: str) -> str:
    return "upload-token:" + source_instance_id


def hmac_account(namespace: str) -> str:
    return "hmac-key:" + namespace


def check_account_name(name: str) -> str:
    if not isinstance(name, str) or not _ACCOUNT_RE.match(name):
        raise ConfigError("keychain account name")
    return name


def check_server_url(url: str) -> str:
    """https 만. 시험·로컬 서버는 http://127.0.0.1·localhost 허용. 경로·쿼리·사용자 정보 금지."""
    if not isinstance(url, str):
        raise ConfigError("server_url")
    p = urlsplit(url)
    if p.username or p.password or p.query or p.fragment or (p.path not in ("", "/")):
        raise ConfigError("server_url")
    host = (p.hostname or "").lower()
    if p.scheme == "https" and host:
        return url.rstrip("/")
    if p.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return url.rstrip("/")
    raise ConfigError("server_url must be https")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def validate_config(cfg: dict) -> dict:
    if cfg.get("config_version") != CONFIG_VERSION:
        raise ConfigError("config_version")
    adapters = cfg.get("adapters") or {}
    for name in adapters:
        if name in BLOCKED_ADAPTERS:
            raise ConfigError("adapter blocked: " + name)
        if name not in ACTIVE_ADAPTERS + SKELETON_ADAPTERS:
            raise ConfigError("unknown adapter: " + name)
    if cfg.get("server_url") is not None:
        check_server_url(cfg["server_url"])
    sid = cfg.get("source_instance_id")
    if sid is not None and not contract.SOURCE_INSTANCE_RE.match(sid):
        raise ConfigError("source_instance_id")
    ns = cfg.get("hmac_namespace")
    if ns is not None and not NAMESPACE_RE.match(ns):
        raise ConfigError("hmac_namespace")
    pr = cfg.get("policy_revision")
    if not isinstance(pr, int) or isinstance(pr, bool) or pr < 1:
        raise ConfigError("policy_revision")
    axes = cfg.get("allowed_axes")
    if not isinstance(axes, list) or not all(isinstance(a, str) and contract.AXIS_ID_RE.match(a) for a in axes):
        raise ConfigError("allowed_axes")
    for k, allowed in (("allowed_actions", contract.ACTIONS), ("allowed_transforms", tuple(contract.TRANSFORMS))):
        v = cfg.get(k)
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x in allowed for x in v)):
            raise ConfigError(k)
    tea = cfg.get("token_expires_at")
    if tea is not None and (not isinstance(tea, str) or len(tea) > 40):
        raise ConfigError("token_expires_at")
    port = cfg.get("viewer_port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1024 <= port <= 65535):
        raise ConfigError("viewer_port")
    check_account_name(cfg.get("keychain_service") or "")
    likes = adapters.get("likes") or {}
    for a in likes.get("axes") or []:
        if a not in axes:
            raise ConfigError("likes.axes not in allowed_axes")
    return cfg


def load(paths: Paths) -> dict:
    if not os.path.exists(paths.config):
        return default_config()
    with open(paths.config, "rb") as f:
        raw = f.read()
    try:
        user = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ConfigError("config.json is not valid JSON") from None
    if not isinstance(user, dict):
        raise ConfigError("config.json must be an object")
    cfg = _merge(default_config(), user)
    return validate_config(cfg)


def save(paths: Paths, cfg: dict) -> None:
    validate_config(cfg)
    paths.ensure()
    data = json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    osutil.atomic_write(paths.config, data)
    osutil.set_private_file(paths.config)


def policy_of(cfg: dict) -> dict:
    """계약 검증기에 넘길 장치 정책."""
    pol = {
        "source_instance_id": cfg.get("source_instance_id"),
        "policy_revision": cfg.get("policy_revision"),
        "allowed_axes": list(cfg.get("allowed_axes") or []),
    }
    if cfg.get("allowed_actions") is not None:
        pol["actions"] = list(cfg["allowed_actions"])
    if cfg.get("allowed_transforms") is not None:
        pol["transforms"] = list(cfg["allowed_transforms"])
    return pol


def apply_device_info(cfg: dict, info: dict, *, expected_stream: str) -> dict:
    """GET /api/imports/device 응답을 검사해 설정에 옮긴다. 모양이 틀리면 ConfigError(값은 메시지에 안 넣음)."""
    if not isinstance(info, dict) or info.get("stream_id") != expected_stream:
        raise ConfigError("device stream_id does not match token")
    ns = info.get("hmac_namespace")
    if not isinstance(ns, str) or not NAMESPACE_RE.match(ns):
        raise ConfigError("device hmac_namespace")
    pr = info.get("policy_revision")
    if not isinstance(pr, int) or isinstance(pr, bool) or pr < 1:
        raise ConfigError("device policy_revision")
    pol = info.get("policy")
    if not isinstance(pol, dict):
        raise ConfigError("device policy")
    actions = [a for a in pol.get("actions", []) if a in contract.ACTIONS]
    transforms = [t for t in pol.get("transform_versions", []) if t in contract.TRANSFORMS]
    axes = pol.get("axes", [])
    if not isinstance(axes, list) or not all(isinstance(a, str) and contract.AXIS_ID_RE.match(a) for a in axes):
        raise ConfigError("device policy axes")
    label = info.get("label")
    out = dict(cfg)
    out.update({"source_instance_id": expected_stream, "hmac_namespace": ns, "policy_revision": pr,
                "allowed_axes": list(dict.fromkeys(axes)), "allowed_actions": actions,
                "allowed_transforms": transforms,
                "device_label": label if isinstance(label, str) and 1 <= len(label) <= 80 else cfg.get("device_label"),
                "token_expires_at": info.get("token_expires_at") if isinstance(info.get("token_expires_at"), str)
                else None})
    return out
