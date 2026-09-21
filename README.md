# FLYHIGH Drone GCS

Python과 MAVLink를 활용한 초보자용 드론 지상관제 교육 프로그램입니다. ArduPilot SITL 시뮬레이션 환경에서 비행 절차를 단계별로 실습합니다.

## 주요 기능

- SITL 실행·종료 및 MAVLink 연결
- 비행 상태 HUD, 비행 모드 변경, ARM/DISARM/TAKEOFF
- 지도 기반 Guided 이동 및 Waypoint CSV 기록
- 단계별 튜토리얼, MAVLink 명령 해설, 실패 원인 안내
- AI Route Assist: 자연어 요청 분석과 교육용 고도 추천
- Battery Safe: 배터리 잔량과 경로를 바탕으로 한 교육용 실행 가능성 추정
- 실습 리포트와 가상 조이스틱 RC override

## 실행 환경과 설치

Windows Desktop, Python 3.x 및 Tkinter가 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe flyhigh_gcs.py
```

시뮬레이션에는 별도 ArduPilot SITL 실행 환경이 필요합니다. 원본 배포본의 `sitl` 폴더를 로컬 프로젝트 폴더 아래에 배치하세요. 코드가 `sitl/ArduCopter.exe`를 실행하므로 해당 실행 파일과 그에 필요한 DLL이 있어야 합니다. 이 저장소에는 외부 실행 파일, DLL, 비행 로그를 포함하지 않습니다.

이미지 파일은 Python 파일과 같은 폴더에 두세요. 지도 및 지명·고도 조회에는 인터넷 연결이 필요합니다. Ollama는 선택 사항이며 실행되지 않을 때에는 규칙 기반 분석을 사용합니다.

## 실습 순서

1. 지도에서 시작 위치를 선택하고 Start SITL을 실행합니다.
2. MAVLink 연결과 GPS 상태를 확인합니다.
3. 가이드에 따라 비행 모드 선택, ARM, TAKEOFF를 진행합니다.
4. 지도 또는 AI Route Assist로 목표 지점을 설정하고 Guided 이동을 실습합니다.
5. 실습을 마친 뒤 Exit SITL로 종료합니다.

## 기능 범위

입력 고도는 HOME 기준 상대고도입니다. Battery Safe는 실제 배터리 소모를 보장하지 않으며, AI Route Assist는 실제 장애물 회피나 자율비행 안전 인증 기능이 아닙니다. 시뮬레이션 기반 교육용 보조 기능입니다.

## 프로젝트 구성

- `flyhigh_gcs.py`: 원본 Python 프로그램(파일명만 정리)
- `requirements.txt`: Python 의존성
- `drone.PNG`, `home.PNG`, `target.PNG`, `logo_icon.png`: UI 이미지
- `README.txt`: 원본 프로그램 설명

## 외부 구성 요소

ArduPilot SITL, pymavlink, tkintermapview, Pillow, requests, Ollama 등은 외부 프로젝트이며 해당 권리와 배포 조건은 각 프로젝트에 따릅니다. 프로그램의 상세 설명은 원본 `README.txt`를 참고하세요.

## 저작권 및 이용 조건

이 프로젝트는 포트폴리오 및 코드 열람 목적으로 공개합니다. 별도의 오픈소스 라이선스는 부여하지 않습니다. 법령 또는 GitHub 이용약관에서 허용하는 범위를 제외한 코드의 이용·복제·배포·수정에는 권리자의 별도 허락이 필요합니다. GitHub 서비스 내 열람 및 포크에는 GitHub 이용약관이 적용됩니다. 외부 구성 요소의 권리는 각 권리자에게 있습니다.
