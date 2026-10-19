# 03. 설치 — Windows

> **상태: 미검증.** 코드는 Windows 를 고려해 썼지만(파일 잠금·경로·인코딩) 깨끗한 Windows PC 에서 처음부터 끝까지 시험하지 않았습니다.
> 막힌 문장과 오류를 `python crm.py diag` 결과와 함께 알려 주시면 지원표에 반영합니다.

**네이티브 Windows(PowerShell)** 기준입니다. WSL 은 별도 환경이라 이 문서를 따르지 말고, WSL 안에서는 [02-install-macos.md](02-install-macos.md) 의 명령을 그대로 쓰되 **WSL 과 Windows 경로·CLI 를 섞지 마세요.**

## 1. Python 3.9 이상

PowerShell 에서:

```powershell
py --version
```

없으면 python.org 에서 Windows 설치 파일을 받아 설치합니다. 설치 화면의 **"Add python.exe to PATH"** 를 체크합니다.
이후 모든 명령의 `python3` 은 `py` 로 바꿔 씁니다.

## 2. Claude Code (정리 기능용)

```powershell
claude --version
```

없으면 Claude Code 공식 설치 안내의 Windows 절을 따릅니다. 설치 뒤 `claude` 를 열어 `/login`.
`doctor` 가 `claude.cmd` 도 찾습니다.

## 3. 패키지 위치와 점검

```powershell
cd "$HOME\Documents\personal-crm"
py crm.py doctor
```

## 4. 데이터 폴더

```powershell
py crm.py init --data "$HOME\crm-data"
py crm.py backup --label first
```

## 5. 확인

```powershell
py crm.py serve --open
```

## 한글이 깨질 때

`crm.py` 는 출력 인코딩을 UTF-8 로 맞춥니다. 그래도 깨지면 Windows Terminal 을 쓰거나 `chcp 65001` 후 다시 실행하세요.

## 이 버전에서 Windows 에 없는 것

예약 실행·알림·자동 수집은 OS 와 무관하게 이 버전에 없습니다. 조용히 실패하는 기능은 없습니다.
