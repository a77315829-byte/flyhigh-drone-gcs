FLYHIGH Drone GCS (초보자용 AI 드론 지상관제 교육 프로그램)

1. 프로그램 개요
FLYHIGH Drone GCS는 초보자를 대상으로 한 Python 기반 드론 지상관제 교육용 응용프로그램이다.
ArduPilot SITL 시뮬레이터와 MAVLink 통신을 활용하여 드론 상태를 시각화하고,
비행 모드 변경, ARM/DISARM, TAKEOFF, 지도 기반 Guided 이동, Waypoint 기록,
초보자 튜토리얼, 명령 해설, 실패 원인 안내, AI Route Assist, Battery Safe 검증 기능을 제공한다.

2. 주요 기능
- ArduPilot SITL 실행 및 종료
- MAVLink TCP 연결 및 드론 상태 데이터 수신
- HUD 기반 상태 표시: ARM 상태, 배터리, 지상속도, 상대고도, 위성 수, 비행 모드
- 비행 모드 변경: STABILIZE, ALT_HOLD, LOITER, GUIDED, AUTO, RTL, LAND
- ARM/DISARM/TAKEOFF 명령 전송
- 지도 클릭 기반 Guided 이동
- Waypoint Log 표시 및 CSV 저장
- 초보자 단계별 비행 절차 안내 및 튜토리얼
- MAVLink 명령 해설 및 실패 원인 안내
- 주소/장소명 기반 위도·경도 변환
- AI Route Assist를 통한 자연어 요청 의도 분석 및 안전 고도 추천
- Battery Safe를 통한 배터리 기반 경로 실행 가능성 검증
- 실습 과정 CSV 리포트 자동 생성
- 지도 스타일 전환 및 실행 중 경로 시각화
- 가상 조이스틱 기반 RC override 실습

3. 실행 환경
- 운영체제: Windows Desktop
- 언어: Python 3.x
- 사용 기종: IBM-PC 호환기종
- 실행 방식: Python 소스파일 실행

4. 필요한 Python 라이브러리
requirements.txt에 명시된 라이브러리를 설치한다.

설치 예시:
pip install -r requirements.txt

5. 외부 프로그램 및 외부 요소
본 프로그램은 다음 외부 프로그램 및 라이브러리를 활용한다.
- ArduPilot SITL(ArduCopter.exe): 드론 비행 시뮬레이션용 외부 프로그램
- pymavlink: MAVLink 통신용 Python 라이브러리
- tkintermapview: 지도 표시용 Python 라이브러리
- Pillow: 이미지 처리용 Python 라이브러리
- requests: 주소 변환 및 Ollama API 호출용 HTTP 라이브러리
- Ollama: 로컬 LLM 기반 AI Route Assist 기능 사용 시 선택적으로 필요한 외부 프로그램

등록 대상은 위 외부 프로그램이나 외부 라이브러리 자체가 아니라,
이를 활용하여 작성한 FLYHIGH Drone GCS의 UI 구성, 실습 흐름, 명령 해설,
Waypoint 기록, AI Route Assist, Battery Safe 검증 로직 및 응용프로그램 소스코드이다.

6. Ollama 사용 여부
Ollama는 필수 실행 요소가 아니다.
Ollama가 실행 중이면 AI Route Assist에서 자연어 비행 요청 의도 분석 기능을 사용할 수 있다.
Ollama가 실행되지 않아도 규칙 기반 분석을 통해 기본적인 안전 고도 추천 및 Battery Safe 검증 기능은 사용할 수 있다.

7. 고도 기준
본 프로그램에서 사용하는 TAKEOFF 고도, Guided 이동 고도, AI 추천 고도, Battery Safe 목표 고도는
모두 HOME 기준 상대고도이다. MAVLink 명령에는 MAV_FRAME_GLOBAL_RELATIVE_ALT_INT 프레임을 사용한다.
따라서 입력 고도는 해발고도 또는 절대고도(AMSL)가 아니라 이륙 지점 기준 높이로 처리된다.

8. Battery Safe 기능의 한계
Battery Safe 기능은 실제 배터리 소모량을 보장하는 기능이 아니다.
현재 배터리 잔량, 목표 거리, HOME 복귀 거리, 상승 고도, 예비 배터리 기준을 바탕으로
경로 실행 가능성을 교육용으로 추정하는 보조 기능이다.
실제 배터리 소모는 바람, 기체 중량, 배터리 노후도, 속도, 조종 방식 등에 따라 달라질 수 있다.

9. AI Route Assist 기능의 한계
AI Route Assist는 실제 장애물 회피 또는 자율비행 안전 인증 기능이 아니다.
사용자 비행 요청의 의도를 분석하고, 샘플 건물 높이 데이터 및 규칙 기반 계산을 통해
교육용 안전 고도를 추천하는 보조 기능이다.

10. 실행 방법
1) Python 3.x를 설치한다.
2) requirements.txt에 명시된 라이브러리를 설치한다.
3) 프로그램 폴더에 필요한 이미지 파일과 sitl 폴더를 배치한다.
4) FLYHIGH Drone GCS Python 파일을 실행한다.
5) 지도 선택 후 Start SITL 버튼을 눌러 시뮬레이터를 실행한다.
6) 초보자 비행 가이드에 따라 MAVLink 연결, GPS 안정화, 모드 선택, ARM, TAKEOFF를 진행한다.
7) 지도 클릭 또는 AI Route Assist를 통해 목표 지점을 설정하고 Guided 이동을 실습한다.
8) 종료 시 Exit SITL 버튼을 눌러 시뮬레이터를 종료하고 리포트를 저장한다.
