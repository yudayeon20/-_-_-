# http://127.0.0.1:5000

# 'X | None' 애노테이션을 문자열로 지연 평가한다.
# 이 줄이 없으면 Python 3.9 에서는 import 만으로 TypeError 가 난다.
from __future__ import annotations

"""Chuncheon bus recommendation web application."""

import os
import sys

from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request


TRANSIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRANSIT_ROOT))

from bus_realtime import (  # noqa: E402
    ChuncheonBisRealtimeClient,
)
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


app = Flask(__name__)
load_dotenv(TRANSIT_ROOT / ".env", override=False)
_realtime_client = None


def get_realtime_client(
    timeout_seconds: float,
) -> ChuncheonBisRealtimeClient:
    global _realtime_client
    if _realtime_client is None:
        _realtime_client = ChuncheonBisRealtimeClient(
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=30,
        )
    return _realtime_client

PLACES = {
    "chiljeon_daewoo": {
        "name": "칠전대우2차아파트",
        "description": "춘천시 칠전동",
        "latitude": 37.8407636,
        "longitude": 127.7117165,
    },
    "bio_center": {
        "name": "춘천바이오산업진흥원",
        "description": "춘천시 후평동",
        "latitude": 37.892294,
        "longitude": 127.7435064,
    },
    "ict_venture_center": {
        "name": "춘천ICT벤처센터",
        "description": "춘천ICT벤처센터",
        "latitude": 37.8904306,
        "longitude": 127.741953,
    },
    "kangwon_university": {
        "name": "강원대학교 춘천캠퍼스",
        "description": "강원대학교 춘천캠퍼스",
        "latitude": 37.8689546,
        "longitude": 127.7450121,
    },
    "youth_library": {
        "name": "춘천시립 청소년도서관",
        "description": "춘천시립 청소년도서관",
        "latitude": 37.8701432,
        "longitude": 127.7111836,
    },
}


CATEGORY_LABELS = {
    "recommended": "추천순",
    "fastest_arrival": "빠른 도착순",
    "direct_first": "직통 우선",
    "least_walking": "최소 도보순",
}


def load_config() -> dict:
    """config.yaml 을 읽고 data 경로를 절대경로로 풀어 준다."""
    with (TRANSIT_ROOT / "config.yaml").open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    for key, value in config.get("data", {}).items():
        path = Path(value)
        if not path.is_absolute():
            path = (TRANSIT_ROOT / path).resolve()
        config["data"][key] = str(path)
    return config


def format_clock(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%m월 %d일 %H:%M")


def format_minutes(seconds: float) -> str:
    total_minutes = max(round(seconds / 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


def format_wait(
    arrival: datetime | None,
    query_datetime: datetime,
) -> str | None:
    if arrival is None:
        return None
    seconds = max(
        (arrival - query_datetime).total_seconds(),
        0,
    )
    return format_minutes(seconds) + " 후 예상"


def serialize_route(
    result: dict,
    query_datetime: datetime,
) -> dict:
    segments = []
    for segment in result["segments"]:
        route_numbers = segment.get(
            "route_number_options",
            [segment["route_number"]],
        )
        bus_options = []
        for option in segment.get("route_options", []):
            boarding_datetime = option.get(
                "boarding_arrival_datetime"
            )
            bus_options.append({
                "route_number": option["route_number"],
                "boarding_time": format_clock(
                    boarding_datetime
                ),
                "wait_text": format_wait(
                    boarding_datetime,
                    query_datetime,
                ),
            })

        segments.append({
            "segment_number": segment["segment_number"],
            "route_numbers": route_numbers,
            "boarding_stop": segment["boarding_stop"]["node_name"],
            "boarding_stop_number": segment["boarding_stop"].get(
                "node_number"
            ),
            "alighting_stop": segment["alighting_stop"]["node_name"],
            "alighting_stop_number": segment["alighting_stop"].get(
                "node_number"
            ),
            "stop_count": segment["stop_count"],
            "in_vehicle_text": format_minutes(
                segment["in_vehicle_seconds"]
            ),
            "boarding_time": format_clock(
                segment.get("boarding_arrival_time")
            ),
            "alighting_time": format_clock(
                segment.get("alighting_arrival_time")
            ),
            "realtime_status": segment.get("realtime_status"),
            "realtime_arrivals": [
                {
                    "arrival_seconds": arrival["arrival_seconds"],
                    "remaining_stop_count": arrival.get(
                        "remaining_stop_count"
                    ),
                }
                for arrival in segment.get("realtime_arrivals", [])
            ],
            "bus_options": bus_options,
            "stops": [
                {
                    "name": stop["node_name"],
                    "number": stop.get("node_number"),
                }
                for stop in segment.get("traversed_stops", [])
            ],
        })

    return {
        "id": "|".join(
            ",".join(segment["route_numbers"])
            for segment in segments
        ),
        "rank": result["rank"],
        "path_type": result["path_type"],
        "tags": result.get("tags", []),
        "categories": result.get("display_categories", []),
        "category_ranks": result.get("category_ranks", {}),
        "arrival_time": format_clock(
            result.get("predicted_destination_arrival_time")
        ),
        "elapsed_text": format_minutes(
            result["total_elapsed_minutes"] * 60
        ),
        "in_vehicle_text": format_minutes(
            result["total_in_vehicle_seconds"]
        ),
        "walking_distance_m": result["total_walking_distance_m"],
        "origin_walking_distance_m": result[
            "origin_walking_distance_m"
        ],
        "origin_walking_minutes": result[
            "origin_walking_minutes"
        ],
        "destination_walking_distance_m": result[
            "destination_walking_distance_m"
        ],
        "destination_walking_minutes": result[
            "destination_walking_minutes"
        ],
        "transfer_walking_distance_m": result.get(
            "transfer_walking_distance_m",
            0,
        ),
        "transfer_walking_minutes": result.get(
            "transfer_walking_minutes",
            0,
        ),
        "transfer_count": result["transfer_count"],
        "transfer_slack_minutes": result.get(
            "transfer_slack_minutes"
        ),
        "segments": segments,
    }


def calculate_routes(origin: dict, destination: dict) -> dict:
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
        common_routes=find_common_routes(
            origin_routes,
            destination_routes,
        ),
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
        config["time"].get(
            "minimum_boarding_buffer_minutes",
            0,
        ),
        config["time"].get("service_day_override"),
        routing.get("walking_speed_m_per_minute", 75),
        routing.get("transfer_buffer_minutes", 2),
    )
    realtime = config.get("realtime", {})
    client = get_realtime_client(
        timeout_seconds=realtime.get("timeout_seconds", 8),
    )
    results = apply_realtime_predictions(
        results,
        client,
        query_datetime,
        realtime.get("arrival_count", 2),
        realtime.get("max_routes_to_enrich", 20),
    )
    results = recalculate_realtime_route_results(
        results,
        query_datetime,
        routing.get("transfer_buffer_minutes", 2),
    )
    max_results_per_category = config["output"].get(
        "max_results_per_category",
        6,
    )
    results = rank_and_select_route_results(
        results,
        query_datetime,
        max_results_per_category,
        routing.get("arrival_tie_minutes", 5),
    )

    serialized = [
        serialize_route(result, query_datetime)
        for result in results
    ]
    categories = {}
    for key, label in CATEGORY_LABELS.items():
        categories[key] = {
            "label": label,
            "routes": sorted(
                [
                    route
                    for route in serialized
                    if key in route["categories"]
                ],
                key=lambda route: route[
                    "category_ranks"
                ].get(key, 999),
            )[:max_results_per_category],
        }

    return {
        "queried_at": query_datetime.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "origin": origin,
        "destination": destination,
        "categories": categories,
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        places=PLACES,
        categories=CATEGORY_LABELS,
    )


# 춘천시 대략 경계.
# 브라우저 GPS 가 엉뚱한 좌표를 주면 정류장 탐색이 통째로 헛돈다.
CHUNCHEON_BOUNDS = {"lat": (37.6, 38.3), "lon": (127.4, 128.1)}


def resolve_place(value, label: str) -> dict:
    """출발지/도착지를 해석한다.

    두 형태를 모두 받는다.
        "bio_center"                                  미리 정의된 장소 key
        {"latitude": .., "longitude": .., "name": ..} 브라우저에서 받은 GPS 좌표

    별도 엔드포인트를 만들지 않고 같은 자리에서 받는다.
    경로 계산은 어차피 좌표만 쓰므로 뒤쪽 코드는 손댈 필요가 없다.
    """
    if isinstance(value, str):
        place = PLACES.get(value)
        if not place:
            raise ValueError(f"지원하지 않는 {label}입니다.")
        return place

    if not isinstance(value, dict):
        raise ValueError(f"{label} 정보가 없습니다.")

    try:
        latitude = float(value["latitude"])
        longitude = float(value["longitude"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{label} 좌표를 읽지 못했습니다.")

    lat_min, lat_max = CHUNCHEON_BOUNDS["lat"]
    lon_min, lon_max = CHUNCHEON_BOUNDS["lon"]
    if not (lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max):
        raise ValueError(
            "현재 위치가 춘천 밖으로 확인됩니다. "
            "실내에서는 위치가 부정확할 수 있으니 장소를 직접 골라 주세요."
        )

    return {
        "name": str(value.get("name") or "현재 위치"),
        "description": str(value.get("description") or "GPS 로 확인한 위치"),
        "latitude": latitude,
        "longitude": longitude,
        "accuracy_m": value.get("accuracy_m"),
    }


@app.post("/api/routes")
def route_api():
    payload = request.get_json(silent=True) or {}
    try:
        origin = resolve_place(payload.get("origin"), "출발지")
        destination = resolve_place(payload.get("destination"), "도착지")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    if (origin["latitude"], origin["longitude"]) == (
            destination["latitude"], destination["longitude"]):
        return jsonify({"error": "출발지와 도착지가 같습니다."}), 400

    try:
        return jsonify(calculate_routes(origin, destination))
    except Exception as error:
        app.logger.exception("route calculation failed")
        return jsonify({
            "error": "경로를 계산하지 못했습니다.",
            "detail": str(error),
        }), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
