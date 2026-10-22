"""PR #20 Q5 — 구조 그래프(DMO) SVG. 명세: notes 「PR20 실행명세」 §8·§9.

순수 모듈이다 — db·네트워크·시각을 부르지 않는다. 입력 = contract.respond(user, viewer) 응답 하나(수신자 무관). 출력 = SVG 문자열.
같은 입력 → 같은 바이트(배치는 응답 순서만 본다, 좌표는 소수 1자리 고정).

- 원 = 오브젝트(응답 objects 순) + 코어(응답 core 순). 고립 노드도 같은 크기·같은 원주 등간격 배치.
- 색 = origin(core·standard·template·custom). 색은 CSS 변수 토큰(--dmo-*) + 밝은 화면 기본값 — 호스트가 tokens_css() 로 토큰을 깐다.
- locked(편입된 표준) = 안쪽 점선 고리 + 고정 문구 「표준 정의 보호」.
- 선 = 관계(참조 필드 하나). 화살표 없음 — 양 끝 까마귀발: 참조를 가진 쪽 = N(세 갈래), 대상 쪽 = N:1 이면 1(두 줄)·N:N 이면 N.
  대상이 없는 관계(to = null — 오브젝트 미지정·대상 정의 없음)는 선을 긋지 않는다.
- 글자·속성(title·aria-label·href)은 응답에 있는 값과 이 모듈의 고정 문구만 쓴다. 노드 이름 = label → std_name → 자리표(ref).
  운영자 비동의 응답엔 label 이 없으니 자리표·편입된 표준명뿐이다. id 는 쓰지 않고 링크는 순번 앵커(node_anchor·rel_anchor)뿐.
"""
import html
import math

ORIGIN_TEXT = {"core": "코어", "standard": "표준", "template": "커스텀", "custom": "커스텀"}   # 명세 §9 구분 표시 용어 = app.html ORIGIN_CHIP(시험이 대조)
CORE_TEXT = {"record": "내 글", "candidate": "검토할 판단", "person": "사람", "source": "출처", "activity": "활동"}   # 뒤 셋 = board 이전 reference 대상
LOCK_TEXT = "표준 정의 보호"

# 토큰 이름: (밝은 화면, 어두운 화면)
TOKENS = {
    "--dmo-ink": ("#2B2824", "#E7EAE6"), "--dmo-muted": ("#6B635A", "#A3AAA6"), "--dmo-line": ("#4A443E", "#BFC6C2"),
    "--dmo-core-bg": ("#F6E3EA", "#3A2230"), "--dmo-core-line": ("#A3406B", "#E08AB0"),
    "--dmo-standard-bg": ("#DCEBE3", "#1C2E26"), "--dmo-standard-line": ("#186A4B", "#4FB08A"),
    "--dmo-template-bg": ("#FBEBD8", "#33281C"), "--dmo-template-line": ("#B5651D", "#E0A262"),
    "--dmo-custom-bg": ("#ECEAE6", "#2A2D2E"), "--dmo-custom-line": ("#6B635A", "#A3AAA6"),
}
NODE_R = 42


def tokens_css(dark_media: bool = False, dark_attr: bool = False) -> str:
    """호스트 화면에 넣을 토큰 CSS. dark_media = OS 다크 설정을 따른다(스크립트 없는 화면) ·
    dark_attr = <html data-theme="dark"> 일 때(theme.js 토글 화면). 둘 다 꺼지면 밝은 값만."""
    light = ";".join(f"{k}:{v[0]}" for k, v in TOKENS.items())
    dark = ";".join(f"{k}:{v[1]}" for k, v in TOKENS.items())
    out = f":root{{{light}}}"
    if dark_media:
        out += f"@media (prefers-color-scheme: dark){{:root:not([data-theme=light]){{{dark}}}}}"
    if dark_attr:
        out += f':root[data-theme="dark"]{{{dark}}}'
    return out


def node_anchor(i: int) -> str:
    """objects[i] 의 앵커 이름(호스트가 같은 id 를 단다)."""
    return f"dmo-o{i + 1}"


def core_anchor(kind: str) -> str:
    return f"dmo-core-{kind}"


def rel_anchor(i: int) -> str:
    """relations[i] 의 앵커 이름."""
    return f"dmo-r{i + 1}"


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def _n(v: float) -> str:
    s = f"{v:.1f}"
    s = s.rstrip("0").rstrip(".") if "." in s else s
    return "0" if s in ("-0", "") else s


def _var(name: str) -> str:
    return f"var({name},{TOKENS[name][0]})"


def _name(d: dict) -> str:
    for k in ("label", "std_name", "ref"):
        if d.get(k):
            return str(d[k])
    return ""


def _short(s: str, n: int = 8) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _model(resp: dict):
    """응답 → (노드, 관계). 본인 응답(id·object_id)과 운영자 응답(ref·from) 둘 다 받는다."""
    objs = resp.get("objects") or []
    nodes, by = [], {}
    for i, o in enumerate(objs):
        bits = [x for x in (o.get("ref"), o.get("label"), o.get("std_name")) if x]
        bits = list(dict.fromkeys(str(x) for x in bits))
        nodes.append({"anchor": node_anchor(i), "name": _name(o), "origin": o.get("origin") or "custom", "locked": bool(o.get("locked")),
                      "title": bits, "fields": o.get("fields") or []})
        for k in (o.get("ref"), o.get("id")):
            if k is not None:
                by[("o", k)] = len(nodes) - 1
    for c in resp.get("core") or []:
        kind = c.get("kind")
        nodes.append({"anchor": core_anchor(kind), "name": CORE_TEXT.get(kind, str(kind)), "origin": "core", "locked": False,
                      "title": [CORE_TEXT.get(kind, str(kind))], "fields": []})
        by[("c", kind)] = len(nodes) - 1
    rels = []
    for i, r in enumerate(resp.get("relations") or []):
        if "from" in r:   # 운영자 응답
            a, fref, to = by.get(("o", r["from"])), r.get("field"), r.get("to")
            b = by.get(("c", to)) if to in CORE_TEXT else by.get(("o", to))
            fd = next((f for f in nodes[a]["fields"] if f.get("ref") == fref), {}) if a is not None else {}
        else:             # 본인 응답
            a, tk = by.get(("o", r.get("object_id"))), r.get("target_kind")
            b = by.get(("o", r.get("target_object_id"))) if tk == "item" else by.get(("c", tk))
            fd = next((f for f in nodes[a]["fields"] if f.get("key") == r.get("field_key")), {}) if a is not None else {}
        rels.append({"i": i, "a": a, "b": b, "field": _name(fd) or str(r.get("field") or r.get("field_key") or ""),
                     "card": r.get("cardinality"), "origin": r.get("origin") or "custom", "locked": bool(r.get("locked"))})
    return nodes, rels


def _foot(px, py, ux, uy, many: bool) -> str:
    """까마귀발 — (px,py) = 원 경계점, (ux,uy) = 원에서 상대 쪽 단위벡터. many = 세 갈래(N), 아니면 두 줄(1)."""
    qx, qy = -uy, ux
    if many:
        cx, cy = px + ux * 15, py + uy * 15
        return " ".join(f"M{_n(cx)},{_n(cy)} L{_n(px + qx * d)},{_n(py + qy * d)}" for d in (-7, 0, 7))
    return " ".join(f"M{_n(px + ux * t + qx * 6)},{_n(py + uy * t + qy * 6)} L{_n(px + ux * t - qx * 6)},{_n(py + uy * t - qy * 6)}"
                    for t in (7, 12))


def _rel_text(nodes, r) -> str:
    a, b = nodes[r["a"]], nodes[r["b"]]
    bits = [f"{a['name']}.{r['field']} — {b['name']}", str(r["card"]), ORIGIN_TEXT.get(r["origin"], r["origin"])]
    return " · ".join(bits + ([LOCK_TEXT] if r["locked"] else []))


def _node_text(nd) -> str:
    bits = nd["title"] + [ORIGIN_TEXT.get(nd["origin"], nd["origin"])]
    return " · ".join(bits + ([LOCK_TEXT] if nd["locked"] else []))


def render(resp: dict, node_href: dict | None = None) -> str:
    """node_href = {노드 앵커: 링크} — 서비스 정의 그래프(운영 Object Manager)가 노드를 상세 화면으로 잇는다. 없으면 순번 앵커(사용자 DMO 바이트 그대로)."""
    nodes, rels = _model(resp)
    node_href = node_href or {}
    k = len(nodes)
    big = 0.0 if k <= 1 else max(140.0, k * 116 / (2 * math.pi))
    pad = NODE_R + 40
    size = 2 * (big + pad)
    c = size / 2
    pos = [(c + big * math.cos(-math.pi / 2 + 2 * math.pi * i / k), c + big * math.sin(-math.pi / 2 + 2 * math.pi * i / k))
           for i in range(k)]
    drawn = [r for r in rels if r["a"] is not None and r["b"] is not None]
    line, ink, muted = _var("--dmo-line"), _var("--dmo-ink"), _var("--dmo-muted")
    out = [f'<svg class="dmo-svg" viewBox="0 0 {_n(size)} {_n(size)}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="구조 그래프 — 자료 종류 {k}개 · 관계 {len(rels)}개" font-family="system-ui,sans-serif">'
           f'<title>구조 그래프 — 자료 종류 {k}개 · 관계 {len(rels)}개</title>']
    pairs = {}
    for r in drawn:
        pairs.setdefault(tuple(sorted((r["a"], r["b"]))), []).append(r)
    for (pa_, pb_), group in sorted(pairs.items(), key=lambda kv: kv[1][0]["i"]):
        for j, r in enumerate(group):
            t = _esc(_rel_text(nodes, r))
            out.append(f'<a href="#{rel_anchor(r["i"])}" aria-label="{t}"><title>{t}</title>')
            s, e = r["a"], r["b"]
            if s == e:   # 자기 참조 — 원 바깥쪽 고리
                x, y = pos[s]
                d = math.hypot(x - c, y - c)
                vx, vy = ((x - c) / d, (y - c) / d) if d else (0.0, -1.0)
                qx, qy = -vy, vx
                p1 = (x + (vx * .8 + qx * .6) * NODE_R, y + (vy * .8 + qy * .6) * NODE_R)
                p2 = (x + (vx * .8 - qx * .6) * NODE_R, y + (vy * .8 - qy * .6) * NODE_R)
                lo = NODE_R + 34 + 10 * j
                out.append(f'<path d="M{_n(p1[0])},{_n(p1[1])} C{_n(x + vx * lo + qx * 30)},{_n(y + vy * lo + qy * 30)} '
                           f'{_n(x + vx * lo - qx * 30)},{_n(y + vy * lo - qy * 30)} {_n(p2[0])},{_n(p2[1])}" '
                           f'fill="none" style="stroke:{line}" stroke-width="1.5"/></a>')
                continue
            (ax, ay), (bx, by) = pos[s], pos[e]
            ln = math.hypot(bx - ax, by - ay) or 1
            ux, uy = (bx - ax) / ln, (by - ay) / ln
            qx, qy = -uy, ux
            off = (j - (len(group) - 1) / 2) * 12 * (1 if (s, e) == (pa_, pb_) else -1)
            da = math.sqrt(max(NODE_R * NODE_R - off * off, 1))
            p1 = (ax + qx * off + ux * da, ay + qy * off + uy * da)
            p2 = (bx + qx * off - ux * da, by + qy * off - uy * da)
            feet = _foot(p1[0], p1[1], ux, uy, True) + " " + _foot(p2[0], p2[1], -ux, -uy, r["card"] == "N:N")
            seg = f'x1="{_n(p1[0])}" y1="{_n(p1[1])}" x2="{_n(p2[0])}" y2="{_n(p2[1])}"'
            out.append(f'<line {seg} style="stroke:{line}" stroke-width="1.4"/>'
                       f'<path d="{feet}" fill="none" style="stroke:{line}" stroke-width="1.4" stroke-linecap="round"/>'
                       f'<line {seg} stroke="transparent" stroke-width="12"/></a>')
    for i, nd in enumerate(nodes):
        x, y = pos[i]
        o = nd["origin"] if nd["origin"] in ORIGIN_TEXT else "custom"
        bg, st = _var(f"--dmo-{o}-bg"), _var(f"--dmo-{o}-line")
        t = _esc(_node_text(nd))
        href = _esc(node_href[nd["anchor"]]) if nd["anchor"] in node_href else f'#{nd["anchor"]}'
        out.append(f'<a href="{href}" aria-label="{t}"><title>{t}</title>'
                   f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{NODE_R}" style="fill:{bg};stroke:{st}" stroke-width="1.6"/>')
        if nd["locked"]:
            out.append(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{NODE_R - 5}" fill="none" style="stroke:{st}" stroke-width="1" '
                       f'stroke-dasharray="3 2"/>')
        top = y - (6 if nd["locked"] else 0)
        out.append(f'<text x="{_n(x)}" y="{_n(top)}" font-size="12.5" font-weight="600" text-anchor="middle" style="fill:{ink}">'
                   f'{_esc(_short(nd["name"]))}</text>'
                   f'<text x="{_n(x)}" y="{_n(top + 14)}" font-size="10" text-anchor="middle" style="fill:{muted}">'
                   f'{_esc(ORIGIN_TEXT.get(o, o))}</text>')
        if nd["locked"]:
            out.append(f'<text x="{_n(x)}" y="{_n(top + 26)}" font-size="8.5" text-anchor="middle" style="fill:{st}">{LOCK_TEXT}</text>')
        out.append("</a>")
    out.append("</svg>")
    return "".join(out)
