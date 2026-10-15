# 05. 일상 운영

매일 써야 하는 도구가 아닙니다. 경험이 생긴 날 1편, 회고·글쓰기·업무 준비 때 열어 보기.

## 기록 1편

Claude Code 에서 「canon-record 스킬로 기록 남길게」 또는 직접:

```bash
python3 canon.py record add --title "무슨 일" --text "있었던 일을 내 말로" --question q-001
python3 canon.py record add --title "회의 메모" --file ~/notes/회의.md     # 동의한 범위 안 파일만
```

원문은 그대로 `journal/` 에 남습니다. 목록은 `python3 canon.py journal`. 끝의 "이게 어디에 붙나" 네 줄 중 `- 판단:` 을 채우면 그게 "내 메모" 후보가 됩니다.

## 정리 — 기록 → 판단 후보 → 질문별 한 장

```bash
python3 canon.py digest --record j-20261015-01 --question q-001             # 모델 사용 (구독)
python3 canon.py digest --record j-20261015-01 --question q-001 --no-model  # 모델 없이
```

- 모델에 보내는 것은 **그 기록 1편 본문과 질문 문장**뿐입니다. 실행 전에 글자 수를 보여 줍니다.
- AI 후보의 근거 인용이 원문에 글자 그대로 없으면 버립니다.
- 같은 정리를 두 번 돌려도 같은 후보를 또 만들지 않습니다.

## 확인 — 내 판단으로 남길지 내가 정한다

```bash
python3 canon.py candidates
python3 canon.py candidate confirm c-0002
python3 canon.py candidate exclude c-0003
python3 canon.py candidate edit c-0002 "내 말로 고친 문장"
```

확인·제외할 때마다 그 질문의 한 장(`sheets/q-001.md`)이 다시 만들어집니다. 이전 판은 `.canon/history/` 에 남습니다.

## 보기

```bash
python3 canon.py serve --open
```

새로고침·탭 전환은 모델을 부르지 않고 파일을 바꾸지 않습니다. 기록 ID 를 누르면 원문이 열립니다.

## 다음 할 일 하나

```bash
python3 canon.py next
```
