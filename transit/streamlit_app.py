"""봄내틔움 — 교육 프로그램 가는 길 (Streamlit 배포용).

로컬:
    streamlit run streamlit_app.py

Streamlit Cloud:
    Main file path 를   교통/transit/transit/streamlit_app.py  로 지정하고
    Settings → Secrets 에 인증키를 넣는다.
        DATA_GO_KR_SERVICE_KEY = "발급받은_인증키"

기존 Flask 앱(web/app.py)과 경로 계산 로직을 100% 공유한다.
이 파일은 화면만 담당한다. 계산은 bus_route / bus_stop / main 이 그대로 한다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

TRANSIT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TRANSIT_ROOT))

# ---------------------------------------------------------------- 인증키
# 우선순위: Streamlit Secrets → 환경변수 → .env
# Cloud 에는 .env 를 올리지 않으므로 Secrets 가 실질적인 공급원이다.
def load_service_key() -> str:
    try:
        key = st.secrets.get("DATA_GO_KR_SERVICE_KEY", "")
    except Exception:
        key = ""
    if not key:
        key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv(TRANSIT_ROOT / ".env", override=False)
            key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
        except ImportError:
            pass
    if key:
        os.environ["DATA_GO_KR_SERVICE_KEY"] = key
    return key


SERVICE_KEY = load_service_key()

import yaml  # noqa: E402

from bus_realtime import TagoRealtimeClient  # noqa: E402
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

# 춘천 대략 경계. GPS 가 엉뚱한 좌표를 주면 경로 계산이 통째로 헛돈다.
CHUNCHEON_BOUNDS = {"lat": (37.6, 38.3), "lon": (127.4, 128.1)}

# 정류장 탐색 반경이 1km 라, GPS 오차가 그보다 크면 엉뚱한 정류장이 잡힌다.
GPS_ACCURACY_WARN_M = 500

CATEGORY_LABELS = {
    "recommended": "추천순",
    "fastest_arrival": "빠른 도착순",
    "direct_first": "직통 우선",
    "least_walking": "최소 도보순",
}


def resolve_data_path(value: str) -> Path:
    """상대경로를 풀고, 한글 파일명의 자소분리 차이까지 흡수한다.

    맥은 파일명을 NFD(자소 분리)로 저장하고 리눅스는 NFC(완성형)를 쓴다.
    맥에서 커밋한 한글 파일명이 Streamlit Cloud(리눅스)에서는 다른 바이트열이라
    "파일을 찾을 수 없습니다"가 난다. 실제로 이것 때문에 배포가 한 번 깨졌다.

    파일명은 영문으로 바꿔 두는 게 근본 해결이고, 이 함수는 안전망이다.
    """
    import unicodedata

    path = Path(value)
    if not path.is_absolute():
        path = (TRANSIT_ROOT / path).resolve()
    if path.exists():
        return path

    parent = path.parent
    if parent.is_dir():
        target = unicodedata.normalize("NFC", path.name)
        for entry in parent.iterdir():
            if unicodedata.normalize("NFC", entry.name) == target:
                return entry
    return path


@st.cache_data(show_spinner=False)
def load_config() -> dict:
    with (TRANSIT_ROOT / "config.yaml").open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    for key, value in config.get("data", {}).items():
        config["data"][key] = str(resolve_data_path(value))
    return config


@st.cache_data(show_spinner=False)
def load_places() -> dict:
    """도착지 후보 = 교육 프로그램이 열리는 장소.

    교육프로그램 파이프라인의 places_master.csv 가 있으면 그걸 쓰고,
    없으면(배포본에 안 담겼으면) 내장 목록으로 떨어진다.
    """
    import csv

    fallback = {
        "춘천바이오산업진흥원": (37.892294, 127.7435064),
        "춘천ICT벤처센터": (37.8904306, 127.741953),
        "강원대학교 춘천캠퍼스": (37.8689546, 127.7450121),
        "춘천시립 청소년도서관": (37.8701432, 127.7111836),
        "춘천시 평생학습관": (37.8495249, 127.7269),
    }

    for candidate in (
        TRANSIT_ROOT / "data" / "places_master.csv",
        TRANSIT_ROOT.parent / "data" / "places_master.csv",
        TRANSIT_ROOT.parents[2] / "교육프로그램" / "output" / "places_master.csv",
    ):
        if not candidate.exists():
            continue
        places = {}
        with candidate.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                try:
                    lat = float(row["latitude"])
                    lon = float(row["longitude"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = (row.get("place_name") or "").strip()
                if name:
                    places[name] = (lat, lon)
        if places:
            return dict(sorted(places.items()))

    return fallback


def in_chuncheon(latitude: float, longitude: float) -> bool:
    lat_min, lat_max = CHUNCHEON_BOUNDS["lat"]
    lon_min, lon_max = CHUNCHEON_BOUNDS["lon"]
    return lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max


def calculate_routes(origin: dict, destination: dict) -> dict:
    """Flask 판 web/app.py 의 calculate_routes 와 같은 절차."""
    config = load_config()
    query_datetime = get_current_seoul_datetime()
    route_stops_path = config["data"]["route_stops_csv"]
    timetable_path = config["data"]["timetable_csv"]
    routing = config["routing"]

    def nearby(place: dict) -> list[dict]:
        return get_candidate_stops(
            csv_path=route_stops_path,
            latitude=place["latitude"],
            longitude=place["longitude"],
            max_distance_m=routing["max_stop_distance_m"],
            max_candidates=routing["max_stop_candidates"],
        )

    origin_routes = get_routes_for_stops(
        stops=nearby(origin),
        route_stops_csv_path=route_stops_path,
        timetable_csv_path=timetable_path,
    )
    destination_routes = get_routes_for_stops(
        stops=nearby(destination),
        route_stops_csv_path=route_stops_path,
        timetable_csv_path=timetable_path,
    )

    direct = analyze_common_routes(
        common_routes=find_common_routes(origin_routes, destination_routes),
        route_stops_csv_path=route_stops_path,
        timetable_csv_path=timetable_path,
    )
    transfer = find_one_transfer_routes(
        origin_stop_routes=origin_routes,
        destination_stop_routes=destination_routes,
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

    realtime = config.get("realtime", {})
    client = TagoRealtimeClient(
        city_code=realtime.get("city_code", "32010"),
        service_key_env=realtime.get("service_key_env", "DATA_GO_KR_SERVICE_KEY"),
        timeout_seconds=realtime.get("timeout_seconds", 8),
        num_of_rows=realtime.get("num_of_rows", 20),
        verify_route_location=realtime.get("verify_route_location", False),
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
    results = rank_and_select_route_results(
        results,
        query_datetime,
        config["output"].get("max_results_per_category", 6),
        routing.get("arrival_tie_minutes", 5),
    )
    return {"results": results, "queried_at": query_datetime}


# ------------------------------------------------------------------ 화면
st.title("🌱 봄내틔움")
st.caption("선택한 교육 프로그램까지, 지금 출발하면 어떻게 가는지 알려드려요.")

if not SERVICE_KEY:
    st.warning(
        "실시간 버스 도착정보 인증키가 설정되지 않았습니다. "
        "시간표 기준 예상 시간만 표시됩니다.\n\n"
        "Streamlit Cloud → Settings → Secrets 에 "
        "`DATA_GO_KR_SERVICE_KEY` 를 넣어 주세요."
    )

places = load_places()

# ---- 출발지 -------------------------------------------------------
st.subheader("어디서 출발하나요?")

origin_mode = st.radio(
    "출발지",
    ["📍 현재 위치", "장소 직접 선택"],
    horizontal=True,
    label_visibility="collapsed",
)

origin = None

if origin_mode == "📍 현재 위치":
    # Streamlit 은 브라우저 위치를 직접 못 읽는다. 컴포넌트가 필요하다.
    # 설치돼 있지 않은 환경(사내망·오프라인)에서도 앱이 죽지 않도록 감싼다.
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
                    "실내에서는 위치가 부정확할 수 있어요. 장소를 직접 골라 주세요."
                )
            else:
                origin = {
                    "name": "현재 위치",
                    "latitude": latitude,
                    "longitude": longitude,
                }
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
            "`streamlit-geolocation` 을 설치하거나 장소를 직접 골라 주세요."
        )

if origin is None and origin_mode == "장소 직접 선택":
    picked = st.selectbox("출발 장소", list(places), index=0, key="origin_place")
    lat, lon = places[picked]
    origin = {"name": picked, "latitude": lat, "longitude": lon}

# ---- 도착지 -------------------------------------------------------
st.subheader("어떤 프로그램에 가나요?")
destination_name = st.selectbox(
    "프로그램 장소",
    list(places),
    index=min(1, len(places) - 1),
    label_visibility="collapsed",
)
dest_lat, dest_lon = places[destination_name]
destination = {"name": destination_name, "latitude": dest_lat, "longitude": dest_lon}

st.divider()
search = st.button("가는 길 찾기", type="primary", use_container_width=True)

if search:
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
                            "/".join(seg["route_numbers"][:2])
                            for seg in route["segments"]
                        )
                        head = f"**{buses}**  ·  {route['elapsed_text']}"
                        if route.get("path_type") == "direct":
                            head += "  ·  직통"
                        with st.container(border=True):
                            st.markdown(head)
                            st.caption(
                                f"도착 예정 {route['arrival_time']}  ·  "
                                f"도보 {int(route['origin_walking_distance_m'] + route['destination_walking_distance_m'])}m"
                            )
                            for seg in route["segments"]:
                                arrivals = seg.get("realtime_arrivals") or []
                                if arrivals:
                                    wait = arrivals[0].get("wait_text", "")
                                    badge = f"🔴 실시간 {wait}"
                                elif seg.get("realtime_status") == "no_active_bus":
                                    badge = "⚪ 현재 운행 중인 차량 없음"
                                else:
                                    badge = "🕘 시간표 기준"
                                st.write(
                                    f"- {seg['boarding_stop']} → {seg['alighting_stop']}  "
                                    f"({seg['stop_count']}정거장, {seg['in_vehicle_text']})  ·  {badge}"
                                )

st.divider()
st.caption(
    "실시간 도착정보 출처: 공공데이터포털 「국토교통부 버스도착정보서비스」  ·  "
    "정류장·시간표: 춘천시 버스 노선 데이터"
)
