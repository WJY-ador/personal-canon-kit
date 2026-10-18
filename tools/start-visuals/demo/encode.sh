#!/usr/bin/env bash
# 데모 합성 — shoot.py 가 찍은 장면 PNG(2x) → 1280w 12fps 프레임(홀드 + 짧은 페이드) → animated WebP + 포스터.
# 실행: ./encode.sh <장면 폴더(shoot.py 의 work/<테마>)> <light|dark> [출력 폴더=server/assets]
#   light → start-demo.webp · start-demo-poster.webp,  dark → start-demo-dark.webp · start-demo-dark-poster.webp
#   start-demo.json 의 그 테마 칸({app_sha256, shot_at})을 장면 폴더 meta.json 으로 갈아 끼운다.
# 필요: ffmpeg · img2webp · cwebp (brew install ffmpeg webp)
# 포스터: 장면 ②(후보 + 인용 형광펜 대조) — 정지 그림 한 장으로 가장 잘 설명된다. ③(남긴 뒤)은 다음 후보로 넘어간 화면이라 정지로는 헷갈린다.
# 키프레임만(-kmax 1): lossy 부분 사각형을 겹쳐 쌓으면 이음매 찌꺼기가 남는다(실측). 같은 프레임은 그래도 합친다
# 용량: 같은 프레임은 img2webp 가 합친다 — 비용은 페이드 구간이 낸다. 넘으면 페이드를 컷(1프레임)으로 → 1080w → MP4 재논의.
set -euo pipefail

WORK="${1:?장면 PNG 가 있는 폴더}"
THEME="${2:?light 또는 dark}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${3:-$HERE/../../../server/assets}"
case "$THEME" in
  light) NAME=start-demo ;;
  dark) NAME=start-demo-dark ;;
  *) echo "실패: 테마는 light 또는 dark"; exit 1 ;;
esac
FPS=12
WIDTH=1280
Q="${Q:-70}"                 # img2webp lossy 품질
POSTER_Q="${POSTER_Q:-75}"
MAX_DEMO=$((800 * 1024))     # 게이트(스펙 잠정값, 실측 431KB). server/test_tools.py ASSET_MAX 와 같은 값
MAX_POSTER=$((60 * 1024))

# 장면 순서·홀드·다음 장면으로 넘어가는 페이드 — 단위는 프레임(12fps). 초로 적으면 프레임 반올림 때문에
# xfade 가 앞 입력의 끝을 넘어 출력이 잘린다(실측). 마지막 s1 은 루프를 닫는 페이드용(홀드 0).
SCENES=(s1 s2 s2b s3 s4 s1)
HOLD=(48 42 14 36 48 0)     # 4초 · 3.5초 · 1.2초 · 3초 · 4초
FADE=(4 1 1 4 4)            # 4프레임 = 0.33초 페이드. s2→s2b(클릭 링)·s2b→s3(누른 뒤)는 1프레임 = 컷

for s in "${SCENES[@]}"; do test -f "$WORK/$s.png" || { echo "실패: $WORK/$s.png 없음"; exit 1; }; done
test -f "$WORK/meta.json" || { echo "실패: $WORK/meta.json 없음(shoot.py 가 만든다)"; exit 1; }

# 입력 길이 L_i = 들어오는 페이드 + 홀드 + 나가는 페이드(프레임). xfade offset_k = (지금까지 출력 프레임 - FADE_k) / FPS.
inputs=(); filters=""; n=${#SCENES[@]}; LEN=()
for ((i = 0; i < n; i++)); do
  fin=0; fout=0
  if ((i > 0)); then fin=${FADE[$((i - 1))]}; fi
  if ((i < n - 1)); then fout=${FADE[$i]}; fi
  LEN[i]=$((fin + HOLD[i] + fout))
  inputs+=(-loop 1 -framerate "$FPS" -i "$WORK/${SCENES[$i]}.png")
  filters+="[$i:v]scale=${WIDTH}:-2:flags=lanczos,format=rgb24,trim=end_frame=${LEN[$i]},setpts=PTS-STARTPTS,settb=1/${FPS}[v$i];"
done
prev="v0"; T=${LEN[0]}
for ((k = 0; k < n - 1; k++)); do
  off=$(awk -v t="$T" -v d="${FADE[$k]}" -v f="$FPS" 'BEGIN{printf "%.6f", (t-d)/f}')
  dur=$(awk -v d="${FADE[$k]}" -v f="$FPS" 'BEGIN{printf "%.6f", d/f}')
  filters+="[$prev][v$((k + 1))]xfade=transition=fade:duration=${dur}:offset=${off}[x$k];"
  prev="x$k"
  T=$((T + LEN[k + 1] - FADE[k]))
done
filters="${filters%;}"
if [ -n "${DEBUG:-}" ]; then echo "$filters" | tr ';' '\n'; echo "총 ${T}프레임"; fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ffmpeg -hide_banner -loglevel error "${inputs[@]}" -filter_complex "$filters" -map "[$prev]" -frames:v "$T" "$TMP/%04d.png"
frames=("$TMP"/*.png)
((${#frames[@]} == T)) || { echo "실패: 프레임 ${#frames[@]}개 — ${T}개여야 해요(xfade 가 잘림)"; exit 1; }
img2webp -loop 0 -kmax 1 -lossy -q "$Q" -m 6 -d $((1000 / FPS)) "${frames[@]}" -o "$TMP/$NAME.webp" >/dev/null 2>&1
ffmpeg -hide_banner -loglevel error -i "$WORK/s2.png" -vf "scale=${WIDTH}:-2:flags=lanczos" "$TMP/poster.png"
cwebp -quiet -q "$POSTER_Q" -m 6 "$TMP/poster.png" -o "$TMP/$NAME-poster.webp"

ds=$(wc -c < "$TMP/$NAME.webp" | tr -d ' '); ps=$(wc -c < "$TMP/$NAME-poster.webp" | tr -d ' ')
kept=$(webpinfo "$TMP/$NAME.webp" | grep -c "Chunk ANMF" || true)
secs=$(awk -v t="$T" -v f="$FPS" 'BEGIN{printf "%.1f", t/f}')
echo "[$THEME] 데모 ${secs}초 · ${T}프레임 → WebP ${kept}프레임(같은 프레임 병합) · q=${Q} → ${ds} B"
echo "포스터(s2) q=${POSTER_Q} → ${ps} B"
((ds <= MAX_DEMO)) || { echo "실패: 데모가 게이트 ${MAX_DEMO} B 를 넘어요 — 페이드→컷 → 1080w → MP4 재논의 순"; exit 1; }
((ps <= MAX_POSTER)) || { echo "실패: 포스터가 ${MAX_POSTER} B 를 넘어요"; exit 1; }
mkdir -p "$OUT"
cp "$TMP/$NAME.webp" "$TMP/$NAME-poster.webp" "$OUT/"
python3 - "$OUT/start-demo.json" "$THEME" "$WORK/meta.json" <<'PY'
import json, sys
from pathlib import Path
out, theme, meta = Path(sys.argv[1]), sys.argv[2], json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
d = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
d = {k: v for k, v in d.items() if k in ("light", "dark")}   # 옛 한 벌 꼴({app_sha256, shot_at})은 버린다
d[theme] = meta
out.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
echo "썼어요: $OUT/$NAME.webp · $NAME-poster.webp · start-demo.json[$THEME]"
