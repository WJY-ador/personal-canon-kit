"""배포 전 패키지 검사 (제작자용) — manifest 대조, 원작자 값·비밀값 스크럽, 문서 속 명령 대조."""
import json
import re
from pathlib import Path

from . import SCHEMA_VERSION, VERSION, paths

SKIP_DIRS = {"__pycache__", ".git", ".DS_Store", ".pytest_cache", "node_modules"}   # node_modules: tools/start-visuals 렌더 도구(제작자 Mac 전용)
SKIP_FILES = {"local.json", ".DS_Store", "CLAUDE.local.md", "settings.local.json"}   # 배포 안 하는 로컬 파일
TEXT_EXT = {".md", ".py", ".json", ".html", ".txt", ".cmd", ".sh", ".ps1", ""}

GENERIC = [
    (re.compile(r"[\w.+-]+@[\w-]+\.(?:com|io|net|kr|co\.kr|org)\b"), "메일 주소"),
    (re.compile(r"/Users/(?!<|you\b|me\b|name\b)[A-Za-z0-9._-]+"), "macOS 홈 경로"),
    (re.compile(r"[A-Z]:\\\\?Users\\\\?(?!<|you\b|name\b)[A-Za-z0-9._-]+"), "Windows 홈 경로"),
    (re.compile(r"sk-ant-[\w-]{6,}|xox[abpr]-[\w-]{6,}|ghp_\w{10,}"), "토큰"),
]


def product_files():
    out = []
    for p in sorted(paths.PRODUCT_ROOT.rglob("*")):
        rel = p.relative_to(paths.PRODUCT_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES or p.is_dir():
            continue
        out.append(rel.as_posix())
    return out


def manifest():
    return json.loads((paths.PRODUCT_ROOT / "manifest.json").read_text(encoding="utf-8"))


def update_manifest():
    m = manifest()
    old = {f["path"]: f for f in m.get("files", [])}
    m["files"] = [old.get(p, {"path": p, "required": True}) for p in product_files() if p != "manifest.json"]
    m["version"], m["schemaVersion"] = VERSION, SCHEMA_VERSION
    (paths.PRODUCT_ROOT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n",
                                                      encoding="utf-8")
    return len(m["files"])


def run(commands, extra_patterns=()):
    issues = []
    m = manifest()
    if m.get("version") != VERSION:
        issues.append(("fail", "manifest.json", f"버전 불일치: manifest {m.get('version')} / 코드 {VERSION}"))
    listed = {f["path"] for f in m.get("files", [])}
    actual = set(product_files()) - {"manifest.json"}
    for f in sorted(listed - actual):
        issues.append(("fail", f, "manifest 에 있는데 패키지에 없음"))
    for f in sorted(actual - listed):
        issues.append(("warn", f, "패키지에 있는데 manifest 에 없음"))
    pats = GENERIC + [(re.compile(re.escape(x), re.I), "원작자 값") for x in extra_patterns if x]
    cmd_re = re.compile(r"canon\.py\s+([a-z][a-z-]*)")
    for rel in sorted(actual | {"manifest.json"}):
        p = paths.PRODUCT_ROOT / rel
        if p.suffix not in TEXT_EXT:
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            if rel == "canonkit/checks.py":
                continue
            for rx, what in pats:
                if rx.search(line):
                    issues.append(("fail", f"{rel}:{i}", f"{what} 의심: {line.strip()[:80]}"))
            if p.suffix in (".md", ".py"):
                for c in cmd_re.findall(line):
                    if c not in commands:
                        issues.append(("fail", f"{rel}:{i}", f"문서에 있는 명령이 CLI 에 없음: {c}"))
    return issues
