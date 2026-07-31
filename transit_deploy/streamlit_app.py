"""봄내틔움 — 교육 프로그램 가는 길 (Streamlit 배포용).

Streamlit Cloud:
    Main file path      streamlit_deploy/streamlit_app.py   (저장소 안의 이 파일)
    Advanced settings   Python 3.11 이상

실시간 도착정보는 web/app.py 와 같은 춘천시 BIS 클라이언트를 씁니다.
경로 계산도 bus_route / bus_stop / main 을 그대로 호출하므로
Flask 판(web/app.py)과 결과가 같습니다. 이 파일은 화면만 담당합니다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 인증키: Streamlit Secrets → 환경변수 → .env 순으로 찾는다.
# Cloud 에는 .env 를 올리지 않으므로 Secrets 가 실질적인 공급원이다.
try:
    _key = st.secrets.get("DATA_GO_KR_SERVICE_KEY", "")
except Exception:
    _key = ""
if not _key:
    _key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
if not _key:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        _key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
    except ImportError:
        pass
if _key:
    os.environ["DATA_GO_KR_SERVICE_KEY"] = _key

import yaml  # noqa: E402

from bus_realtime import ChuncheonBisRealtimeClient  # noqa: E402
from bus_route import (  # noqa: E402
    analyze_common_routes,
    find_common_routes,
    find_one_transfer_routes,
    get_routes_for_stops,
)
from bus_stop import get_candidate_stops  # noqa: E402
from bus_timetable import get_current_seoul_datetime  # noqa: E402
from main import (  # noqa: E402
    apply_realtime_predictions,
    apply_timetable_predictions,
    build_direct_route_results,
    build_transfer_route_results,
    combine_route_results,
    rank_and_select_route_results,
    recalculate_realtime_route_results,
)

st.set_page_config(page_title="봄내틔움 · 가는 길", page_icon="🌱", layout="centered")

# 춘천 대략 경계. GPS 가 엉뚱한 좌표를 주면 정류장 탐색이 통째로 헛돈다.
CHUNCHEON_BOUNDS = {"lat": (37.6, 38.3), "lon": (127.4, 128.1)}

# 정류장 탐색 반경이 1km 라, GPS 오차가 그보다 크면 엉뚱한 정류장이 잡힌다.
GPS_ACCURACY_WARN_M = 500

CATEGORY_LABELS = {
    "recommended": "추천순",
    "fastest_arrival": "빠른 도착순",
    "direct_first": "직통 우선",
    "least_walking": "최소 도보순",
}

PLACES = {
    "춘천바이오산업진흥원": (37.892294, 127.7435064),
    "춘천ICT벤처센터": (37.8904306, 127.741953),
    "강원대학교 춘천캠퍼스": (37.8689546, 127.7450121),
    "춘천시립 청소년도서관": (37.8701432, 127.7111836),
    "칠전대우2차아파트": (37.8407636, 127.7117165),
}


@st.cache_data(show_spinner=False)
def load_config() -> dict:
    with (ROOT / "config.yaml").open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    for key, value in config.get("data", {}).items():
        path = Path(value)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        config["data"][key] = str(path)
    return config


def in_chuncheon(latitude: float, longitude: float) -> bool:
    lat_min, lat_max = CHUNCHEON_BOUNDS["lat"]
    lon_min, lon_max = CHUNCHEON_BOUNDS["lon"]
    return lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max


def calculate_routes(origin: dict, destination: dict) -> dict:
    """경로 계산. Flask 판 web/app.py 와 같은 절차다.

    ## 속도 때문에 두 군데를 나눠 처리한다

    1) 환승 탐색은 출발 후보 × 도착 후보 조합이라 후보 수에 폭발적으로 반응한다.
       실측: 후보 10 → 29초 / 6 → 3.6초.
       그런데 후보를 줄이면 직통 경로를 놓친다(2건 → 0건).
       → 직통은 넓게(10), 환승만 좁게(6) 본다.

    2) 실시간은 시간표로 순위를 먼저 추린 뒤 붙인다.
       전체 경로에 붙이면 조회 대상이 5배로 늘어난다.
    """
    config = load_config()
    query_datetime = get_current_seoul_datetime()
    route_stops_path = config["data"]["route_stops_csv"]
    timetable_path = config["data"]["timetable_csv"]
    routing = config["routing"]

    def nearby(place: dict, limit: int) -> list[dict]:
        return get_candidate_stops(
            csv_path=route_stops_path,
            latitude=place["latitude"],
            longitude=place["longitude"],
            max_distance_m=routing["max_stop_distance_m"],
            max_candidates=limit,
        )

    def routes_of(stops: list[dict]) -> dict:
        return get_routes_for_stops(
            stops=stops,
            route_stops_csv_path=route_stops_path,
            timetable_csv_path=timetable_path,
        )

    wide = routing["max_stop_candidates"]
    narrow = routing.get("max_transfer_stop_candidates", min(6, wide))

    direct = analyze_common_routes(
        common_routes=find_common_routes(
            routes_of(nearby(origin, wide)),
            routes_of(nearby(destination, wide)),
        ),
        route_stops_csv_path=route_stops_path,
        timetable_csv_path=timetable_path,
    )
    transfer = find_one_transfer_routes(
        origin_stop_routes=routes_of(nearby(origin, narrow)),
        destination_stop_routes=routes_of(nearby(destination, narrow)),
        route_stops_csv_path=route_stops_path,
        timetable_csv_path=timetable_path,
        max_transfer_walk_m=routing["max_transfer_walk_m"],
        max_results=routing.get("max_transfer_results", 100),
    )

    results = combine_route_results(
        build_direct_route_results(direct),
        build_transfer_route_results(transfer),
        routing["max_in_vehicle_minutes"],
    )
    results = apply_timetable_predictions(
        results,
        timetable_path,
        query_datetime,
        config["time"].get("upcoming_arrival_count", 2),
        config["time"].get("minimum_boarding_buffer_minutes", 0),
        config["time"].get("service_day_override"),
        routing.get("walking_speed_m_per_minute", 75),
        routing.get("transfer_buffer_minutes", 2),
    )

    per_category = config["output"].get("max_results_per_category", 6)
    tie = routing.get("arrival_tie_minutes", 5)
    results = rank_and_select_route_results(results, query_datetime, per_category, tie)

    realtime = config.get("realtime", {})
    client = ChuncheonBisRealtimeClient(
        timeout_seconds=realtime.get("timeout_seconds", 8),
        cache_ttl_seconds=30,
    )
    results = apply_realtime_predictions(
        results,
        client,
        query_datetime,
        realtime.get("arrival_count", 2),
        realtime.get("max_routes_to_enrich", 500),
    )
    results = recalculate_realtime_route_results(
        results, query_datetime, routing.get("transfer_buffer_minutes", 2)
    )
    results = rank_and_select_route_results(results, query_datetime, per_category, tie)
    return {"results": results, "queried_at": query_datetime}


# ------------------------------------------------------------------ 화면
st.title("🌱 봄내틔움")
st.caption("선택한 교육 프로그램까지, 지금 출발하면 어떻게 가는지 알려드려요.")

st.subheader("어디서 출발하나요?")
origin_mode = st.radio(
    "출발지",
    ["📍 현재 위치", "장소 직접 선택"],
    horizontal=True,
    label_visibility="collapsed",
)

origin = None
if origin_mode == "📍 현재 위치":
    # Streamlit 은 브라우저 위치를 직접 못 읽어 컴포넌트가 필요하다.
    # 없는 환경에서도 앱이 죽지 않도록 감싼다.
    try:
        from streamlit_geolocation import streamlit_geolocation

        st.caption("아래 버튼을 누르고 브라우저의 위치 권한을 허용해 주세요.")
        location = streamlit_geolocation()
        if location and location.get("latitude") is not None:
            latitude = float(location["latitude"])
            longitude = float(location["longitude"])
            accuracy = location.get("accuracy")
            if not in_chuncheon(latitude, longitude):
                st.error(
                    f"현재 위치가 춘천 밖으로 확인됩니다 ({latitude:.4f}, {longitude:.4f}). "
                    "실내에서는 부정확할 수 있어요. 장소를 직접 골라 주세요."
                )
            else:
                origin = {"name": "현재 위치", "latitude": latitude, "longitude": longitude}
                if accuracy and accuracy > GPS_ACCURACY_WARN_M:
                    st.warning(
                        f"현재 위치 확인 (오차 약 {int(accuracy)}m) — "
                        "정류장 탐색 반경이 1km 라 실내에서는 결과가 어긋날 수 있어요."
                    )
                else:
                    acc = f" (오차 약 {int(accuracy)}m)" if accuracy else ""
                    st.success(f"현재 위치를 확인했어요{acc}")
    except ImportError:
        st.info(
            "이 환경에는 위치 컴포넌트가 없어 현재 위치를 쓸 수 없습니다. "
            "장소를 직접 골라 주세요."
        )

if origin is None and origin_mode == "장소 직접 선택":
    picked = st.selectbox("출발 장소", list(PLACES), index=4)
    lat, lon = PLACES[picked]
    origin = {"name": picked, "latitude": lat, "longitude": lon}

st.subheader("어떤 프로그램에 가나요?")
destination_name = st.selectbox(
    "프로그램 장소", list(PLACES), index=0, label_visibility="collapsed"
)
dest_lat, dest_lon = PLACES[destination_name]
destination = {"name": destination_name, "latitude": dest_lat, "longitude": dest_lon}

st.divider()
if st.button("가는 길 찾기", type="primary", use_container_width=True):
    if origin is None:
        st.error("출발지를 먼저 정해 주세요.")
    elif (origin["latitude"], origin["longitude"]) == (dest_lat, dest_lon):
        st.error("출발지와 도착지가 같습니다.")
    else:
        with st.spinner("버스 경로와 실시간 도착정보를 확인하는 중…"):
            try:
                payload = calculate_routes(origin, destination)
            except Exception as error:  # noqa: BLE001
                st.error(f"경로를 계산하지 못했습니다: {error}")
                payload = None

        if payload:
            results = payload["results"]
            st.caption(
                f"{origin['name']} → {destination['name']}  ·  "
                f"{payload['queried_at'].strftime('%Y-%m-%d %H:%M')} 기준"
            )
            if not results:
                st.warning(
                    "버스로 갈 수 있는 경로를 찾지 못했어요. "
                    "출발지 주변 1km 안에 정류장이 없거나, 운행이 끝난 시간일 수 있습니다."
                )

            tabs = st.tabs(list(CATEGORY_LABELS.values()))
            for tab, key in zip(tabs, CATEGORY_LABELS):
                with tab:
                    picked = [r for r in results if key in r.get("categories", [])]
                    picked.sort(key=lambda r: r.get("category_ranks", {}).get(key, 999))
                    if not picked:
                        st.info("이 기준에 맞는 경로가 없어요.")
                        continue
                    for route in picked[:5]:
                        buses = " → ".join(
                            "/".join(str(n) for n in seg["route_numbers"][:2])
                            for seg in route["segments"]
                        )
                        head = f"**{buses}**  ·  {route['elapsed_text']}"
                        if route.get("path_type") == "direct":
                            head += "  ·  직통"
                        with st.container(border=True):
                            st.markdown(head)
                            walk = int(
                                route["origin_walking_distance_m"]
                                + route["destination_walking_distance_m"]
                            )
                            st.caption(
                                f"도착 예정 {route['arrival_time']}  ·  도보 {walk}m"
                            )
                            for seg in route["segments"]:
                                arrivals = seg.get("realtime_arrivals") or []
                                if arrivals:
                                    badge = f"🔴 실시간 {arrivals[0].get('wait_text', '')}"
                                elif seg.get("realtime_status") == "no_active_bus":
                                    badge = "⚪ 지금 운행 중인 차량 없음"
                                else:
                                    badge = "🕘 시간표 기준"
                                st.write(
                                    f"- {seg['boarding_stop']} → {seg['alighting_stop']}  "
                                    f"({seg['stop_count']}정거장, {seg['in_vehicle_text']})"
                                    f"  ·  {badge}"
                                )

st.divider()
st.caption("정류장·시간표: 춘천시 버스 노선 데이터  ·  실시간 도착정보: 춘천시 버스정보시스템")
