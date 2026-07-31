
# 'X | None' 애노테이션을 문자열로 지연 평가한다.
# 이 줄이 없으면 Python 3.9 에서는 import 만으로 TypeError 가 난다.
from __future__ import annotations
import csv
import math

from functools import lru_cache
from pathlib import Path


def haversine_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """두 위·경도 사이의 직선거리를 m 단위로 반환한다."""
    earth_radius = 6_371_000

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    return earth_radius * 2 * math.atan2(
        math.sqrt(value),
        math.sqrt(1 - value),
    )


def _read_csv(csv_path: str) -> list[dict]:
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(
            f"CSV 파일을 찾을 수 없습니다: {path}"
        )

    last_error = None

    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            with path.open(
                "r",
                encoding=encoding,
                newline="",
            ) as file:
                return list(csv.DictReader(file))
        except UnicodeDecodeError as error:
            last_error = error

    raise RuntimeError(
        f"CSV 인코딩을 확인할 수 없습니다: {path}"
    ) from last_error


@lru_cache(maxsize=4)
def load_unique_stops(
    csv_path: str,
) -> tuple[dict, ...]:
    """
    위치 검색용 정류장 목록을 만든다.

    같은 정류장 ID가 여러 노선에 반복되더라도 한 번만 유지한다.
    방향 구분은 bus_route.py에서 수행한다.
    """
    stops_by_id = {}

    for row in _read_csv(csv_path):
        node_id = str(row.get("정류장", "")).strip()

        if not node_id:
            continue

        try:
            latitude = float(row["위도"])
            longitude = float(row["경도"])
        except (KeyError, TypeError, ValueError):
            continue

        if node_id in stops_by_id:
            continue

        node_number = str(
            row.get("정류장 번호", "")
        ).strip()

        stops_by_id[node_id] = {
            "node_id": node_id,
            "node_number": node_number or None,
            "node_name": str(
                row.get("정류장명", "")
            ).strip(),
            "latitude": latitude,
            "longitude": longitude,
        }

    return tuple(stops_by_id.values())


def get_candidate_stops(
    csv_path: str,
    latitude: float,
    longitude: float,
    max_distance_m: float,
    max_candidates: int,
) -> list[dict]:
    """입력 좌표 주변의 정류장을 가까운 순으로 반환한다."""
    stops = []

    for stop in load_unique_stops(csv_path):
        distance_m = haversine_distance(
            latitude,
            longitude,
            stop["latitude"],
            stop["longitude"],
        )

        if distance_m <= max_distance_m:
            stops.append({
                **stop,
                "distance_m": round(distance_m, 1),
            })

    stops.sort(key=lambda stop: stop["distance_m"])
    return stops[:max_candidates]