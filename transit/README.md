# 봄내틔움 — 교육 프로그램 가는 길

춘천시 청소년 교육 프로그램까지의 **버스 경로와 실시간 도착정보**를 안내합니다.
현재 위치(GPS)에서 프로그램 장소까지, 직통·환승 경로를 소요시간 순으로 보여줍니다.

## 데이터 출처

| 데이터 | 출처 |
|---|---|
| 버스 실시간 도착정보 | 공공데이터포털 「국토교통부 버스도착정보서비스」 (1613000) |
| 정류장·노선·누적 소요시간 | 춘천시 버스 노선 데이터 |
| 기점 출발 시간표 | 춘천시 버스 출발시간 데이터 |

실시간 정보는 **공공데이터포털 정식 API**만 사용합니다.

---

## 폴더 구조

```
.
├── requirements.txt          의존성 (Streamlit Cloud 가 여기서 읽는다)
├── data/
│   ├── chuncheon_bus_route_stops_latest.csv    정류장·노선 16,329행
│   └── chuncheon_bus_departure_times.csv       기점 출발시간표
└── transit/
    ├── streamlit_app.py      ← Streamlit 배포용 진입점
    ├── config.yaml           경로 탐색 조건, 실시간 설정
    ├── bus_stop.py           좌표 → 주변 정류장
    ├── bus_route.py          정류장 → 노선, 직통·환승 탐색
    ├── bus_timetable.py      시간표 기반 도착 예측
    ├── bus_realtime.py       공공데이터 실시간 도착정보
    ├── main.py               경로 조합·순위 결정
    └── web/                  Flask 로컬 실행판 (선택)
```

`streamlit_app.py` 는 화면만 담당하고, 경로 계산은 `bus_*` / `main` 모듈이 그대로 합니다.
Flask 판(`web/app.py`)과 계산 로직이 동일합니다.

---

## Streamlit Cloud 배포

| 항목 | 값 |
|---|---|
| Main file path | `transit/streamlit_app.py` |
| Python version | **3.11 이상** |

Advanced settings → **Secrets** 에 인증키를 넣습니다.

```toml
DATA_GO_KR_SERVICE_KEY = "발급받은_인증키"
```

키가 없어도 앱은 동작합니다. 실시간 대신 **시간표 기준 예상 시간**만 표시됩니다.

---

## 로컬 실행

```bash
pip install -r requirements.txt

cp transit/.env.example transit/.env
# .env 를 열어 DATA_GO_KR_SERVICE_KEY 를 채운다

streamlit run transit/streamlit_app.py
```

Flask 판을 쓰려면:

```bash
python transit/web/app.py     # http://127.0.0.1:5000
```

---

## 알아둘 것

### 실시간 도착정보 ID 형식

CSV 의 정류장·노선 ID 는 순수 숫자(`250000100`)지만, 공공데이터 API 는
도시 접두어가 붙은 ID(`CCB250000100`)를 받습니다.

접두어 없이 보내면 **HTTP 200 에 `totalCount: 0`** 이 돌아옵니다.
오류가 아니라 "조회 결과 없음"으로 보여서, 실시간이 제공되지 않는 것처럼 착각하기 쉽습니다.
`bus_realtime.tago_id()` 가 이 변환을 담당합니다.

### 파일명은 영문으로

한글 파일명은 맥(NFD 자소분리)과 리눅스(NFC 완성형)에서 서로 다른 바이트열입니다.
맥에서 커밋한 한글 파일을 Streamlit Cloud(리눅스)가 못 찾습니다.
`data/` 의 CSV 를 영문명으로 둔 이유입니다.

### GPS 위치

- Streamlit Cloud 는 https 라 브라우저 위치 권한이 정상 동작합니다.
- 로컬 Flask 판은 `localhost` 에서만 됩니다. PC 의 IP 로 접속하면 브라우저가 막습니다.
- 실내 wifi 측위는 오차가 큽니다. 정류장 탐색 반경이 1km 라, 오차가 그보다 크면
  엉뚱한 정류장이 잡힙니다. 오차 500m 초과 시 경고를 표시합니다.

### 실시간 조회 범위

`config.yaml` 의 `max_routes_to_enrich` 를 작게 두면 안 됩니다.
실시간 보강이 **순위 매기기 전**에 일어나기 때문에, 조회하지 않은 경로가
재정렬 후 1순위로 올라오면 화면 최상단에 실시간이 비게 됩니다.

호출량 걱정은 크지 않습니다. 호출 단위가 경로가 아니라 고유 (정류장, 노선) 쌍이고
클라이언트가 캐시합니다. 경로 109개를 전부 조회해도 실제 호출은 43회입니다.
