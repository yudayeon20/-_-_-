
# X | None 애노테이션을 문자열로 지연 평가한다.
# 이 한 줄이 없으면 Python 3.9 에서 import 만으로 TypeError 가 난다.
from __future__ import annotations
import csv
import re

from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from bus_stop import haversine_distance
from bus_timetable import (
    load_direction_definitions,
    normalize_text,
)


def normalize_route_number(value: str) -> str:
    value = str(value).strip()

    match = re.fullmatch(
        r"(\d{1,2})월\s*(\d{1,2})일",
        value,
    )

    if match:
        return f"{int(match.group(1))}-{int(match.group(2))}"

    return value


def _to_float(
    value,
    default: float | None = None,
) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _terminal_name_matches(
    stop_name: str,
    terminal_name: str,
) -> bool:
    """
    시간표 기·종점명과 정류장명을 비교한다.

    먼저 정규화 후 완전일치를 사용하고,
    실패할 때 한쪽 문자열이 다른 쪽에 포함되는지도 확인한다.
    """
    stop_key = normalize_text(stop_name)
    terminal_key = normalize_text(terminal_name)

    if not stop_key or not terminal_key:
        return False

    if stop_key == terminal_key:
        return True

    return (
        stop_key in terminal_key
        or terminal_key in stop_key
    )


def _build_direction_slice(
    raw_stops: list[dict],
    direction: dict,
) -> list[dict] | None:
    """
    전체 왕복 정류장 목록에서 한 방향의 연속 구간을 찾는다.

    예:
    상공회의소 → ... → 화물공영차고지 → ... → 상공회의소

    direction 1은 첫 상공회의소부터 첫 화물공영차고지까지,
    direction 2는 뒤쪽 화물공영차고지부터 마지막 상공회의소까지
    선택한다.

    같은 이름이 여러 번 나오면 가능한 start < end 조합 중
    가장 짧은 양의 구간을 선택한다. 이 방식으로 회차지 중복
    정류장이 있을 때 방향 경계를 넘지 않도록 한다.
    """
    start_indexes = [
        index
        for index, stop in enumerate(raw_stops)
        if _terminal_name_matches(
            stop["node_name"],
            direction["departure_terminal"],
        )
    ]

    end_indexes = [
        index
        for index, stop in enumerate(raw_stops)
        if _terminal_name_matches(
            stop["node_name"],
            direction["arrival_terminal"],
        )
    ]

    candidates = []

    for start_index in start_indexes:
        for end_index in end_indexes:
            if end_index <= start_index:
                continue

            span = end_index - start_index

            candidates.append(
                (
                    span,
                    start_index,
                    end_index,
                )
            )

    if not candidates:
        return None

    _, start_index, end_index = min(
        candidates,
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
    )

    selected = raw_stops[
        start_index:end_index + 1
    ]

    if len(selected) < 2:
        return None

    direction_start_cumulative_sec = selected[
        0
    ]["raw_cumulative_time_sec"]

    result = []

    for direction_order, stop in enumerate(
        selected,
        start=1,
    ):
        direction_time_sec = (
            stop["raw_cumulative_time_sec"]
            - direction_start_cumulative_sec
        )

        result.append({
            **stop,
            "direction_order": direction_order,
            "direction_cumulative_time_sec": round(
                max(direction_time_sec, 0),
                1,
            ),
        })

    return result


def _build_full_direction(
    raw_stops: list[dict],
) -> list[dict]:
    """시간표 방향 정의가 없거나 하나뿐인 순환 노선은 CSV 전체 순서를 사용한다."""
    if not raw_stops:
        return []

    start_cumulative_sec = raw_stops[0][
        "raw_cumulative_time_sec"
    ]
    return [
        {
            **stop,
            "direction_order": direction_order,
            "direction_cumulative_time_sec": round(
                max(
                    stop["raw_cumulative_time_sec"]
                    - start_cumulative_sec,
                    0,
                ),
                1,
            ),
        }
        for direction_order, stop in enumerate(
            raw_stops,
            start=1,
        )
    ]


@lru_cache(maxsize=4)
def load_directional_bus_indexes(
    route_stops_csv_path: str,
    timetable_csv_path: str,
) -> tuple[dict, dict]:
    """
    전체 왕복 노선 CSV를 시간표의 direction_code별 독립 노선으로 분리한다.

    반환:
      directional_stops:
        route_key -> 방향별 정류장 목록

      directional_info:
        route_key -> 노선·방향 기본정보
    """
    rows = _read_csv(route_stops_csv_path)

    if rows and "누적예상시간_초" not in rows[0]:
        raise KeyError(
            "정류장 CSV에 '누적예상시간_초' 컬럼이 없습니다."
        )

    raw_route_stops = defaultdict(list)
    raw_route_numbers = {}

    for row in rows:
        route_id = str(row.get("노선", "")).strip()
        node_id = str(row.get("정류장", "")).strip()

        if not route_id or not node_id:
            continue

        try:
            node_order = int(
                float(str(row["정류장순서"]).strip())
            )
            longitude = float(row["경도"])
            latitude = float(row["위도"])
        except (KeyError, TypeError, ValueError):
            continue

        cumulative_time_sec = _to_float(
            row.get("누적예상시간_초"),
            default=None,
        )

        if cumulative_time_sec is None:
            continue

        node_number = str(
            row.get("정류장 번호", "")
        ).strip()

        raw_route_numbers[route_id] = (
            normalize_route_number(
                row.get("노선번호", "")
            )
        )

        raw_route_stops[route_id].append({
            "node_id": node_id,
            "node_number": node_number or None,
            "node_name": str(
                row.get("정류장명", "")
            ).strip(),
            "node_order": node_order,
            "latitude": latitude,
            "longitude": longitude,
            "raw_cumulative_time_sec": (
                cumulative_time_sec
            ),
        })

    for route_id in raw_route_stops:
        unique = {}

        for stop in raw_route_stops[route_id]:
            unique[
                (
                    stop["node_order"],
                    stop["node_id"],
                )
            ] = stop

        raw_route_stops[route_id] = sorted(
            unique.values(),
            key=lambda stop: stop["node_order"],
        )

    direction_definitions = load_direction_definitions(
        timetable_csv_path
    )

    directional_stops = {}
    directional_info = {}

    for route_id, raw_stops in raw_route_stops.items():
        definitions = direction_definitions.get(
            route_id,
            (),
        )

        if not definitions:
            definitions = ({
                "route_id": route_id,
                "route_no": raw_route_numbers.get(
                    route_id,
                    "",
                ),
                "route_type": None,
                "direction_code": "1",
                "departure_terminal": raw_stops[0][
                    "node_name"
                ],
                "arrival_terminal": raw_stops[-1][
                    "node_name"
                ],
            },)

        for direction in definitions:
            if len(definitions) == 1:
                direction_stops = _build_full_direction(
                    raw_stops
                )
            else:
                direction_stops = _build_direction_slice(
                    raw_stops,
                    direction,
                )

            if not direction_stops:
                continue

            route_key = (
                route_id,
                direction["direction_code"],
            )

            route_number = (
                direction.get("route_no")
                or raw_route_numbers.get(route_id)
                or ""
            )

            directional_stops[route_key] = (
                direction_stops
            )

            directional_info[route_key] = {
                "route_key": route_key,
                "route_id": route_id,
                "route_number": normalize_route_number(
                    route_number
                ),
                "route_type": direction.get(
                    "route_type"
                ),
                "direction_code": direction[
                    "direction_code"
                ],
                "departure_terminal": direction[
                    "departure_terminal"
                ],
                "arrival_terminal": direction[
                    "arrival_terminal"
                ],
                "start_stop_name": direction_stops[
                    0
                ]["node_name"],
                "end_stop_name": direction_stops[
                    -1
                ]["node_name"],
            }

    return directional_stops, directional_info


@lru_cache(maxsize=4)
def build_stop_routes_index(
    route_stops_csv_path: str,
    timetable_csv_path: str,
) -> dict[str, tuple[dict, ...]]:
    """
    정류장 ID → 통과하는 방향 노선 목록 인덱스.

    같은 route_id라도 direction_code가 다르면 별도 노선이다.
    """
    directional_stops, directional_info = (
        load_directional_bus_indexes(
            route_stops_csv_path,
            timetable_csv_path,
        )
    )

    index = defaultdict(list)

    for route_key, stops in directional_stops.items():
        route = directional_info[route_key]

        for stop in stops:
            index[stop["node_id"]].append({
                **route,
                "route_stop": stop,
            })

    return {
        node_id: tuple(routes)
        for node_id, routes in index.items()
    }


def get_routes_for_stops(
    stops: list[dict],
    route_stops_csv_path: str,
    timetable_csv_path: str,
) -> list[dict]:
    index = build_stop_routes_index(
        route_stops_csv_path,
        timetable_csv_path,
    )

    results = []

    for candidate_stop in stops:
        routes = []

        for route in index.get(
            candidate_stop["node_id"],
            (),
        ):
            route_stop = {
                **route["route_stop"],
                "distance_m": candidate_stop.get(
                    "distance_m",
                    0,
                ),
            }

            routes.append({
                key: value
                for key, value in route.items()
                if key != "route_stop"
            } | {
                "matched_stop": route_stop,
            })

        if routes:
            results.append({
                "stop": candidate_stop,
                "routes": routes,
            })

    return results


def find_common_routes(
    origin_stop_routes: list[dict],
    destination_stop_routes: list[dict],
) -> list[dict]:
    """
    같은 route_id뿐 아니라 같은 direction_code까지 일치해야 직행이다.
    """
    destination_index = defaultdict(list)

    for destination_data in destination_stop_routes:
        for route in destination_data["routes"]:
            destination_index[
                route["route_key"]
            ].append(route)

    results = []
    seen = set()

    for origin_data in origin_stop_routes:
        for origin_route in origin_data["routes"]:
            route_key = origin_route["route_key"]
            origin_stop = origin_route["matched_stop"]

            for destination_route in destination_index.get(
                route_key,
                (),
            ):
                destination_stop = destination_route[
                    "matched_stop"
                ]

                if (
                    origin_stop["direction_order"]
                    >= destination_stop["direction_order"]
                ):
                    continue

                key = (
                    route_key,
                    origin_stop["node_id"],
                    origin_stop["direction_order"],
                    destination_stop["node_id"],
                    destination_stop[
                        "direction_order"
                    ],
                )

                if key in seen:
                    continue

                seen.add(key)

                results.append({
                    **origin_route,
                    "origin_stop": origin_stop,
                    "destination_stop": destination_stop,
                })

    return results


def calculate_in_vehicle_time(
    origin_stop: dict,
    destination_stop: dict,
) -> tuple[float, float]:
    origin_time = _to_float(
        origin_stop.get(
            "direction_cumulative_time_sec"
        ),
        default=None,
    )

    destination_time = _to_float(
        destination_stop.get(
            "direction_cumulative_time_sec"
        ),
        default=None,
    )

    if origin_time is None or destination_time is None:
        raise ValueError(
            "방향 기준 누적예상시간이 없습니다."
        )

    seconds = destination_time - origin_time

    if seconds < 0:
        raise ValueError(
            "하차 정류장이 탑승 정류장보다 앞에 있습니다."
        )

    return round(seconds, 1), round(seconds / 60, 2)


def analyze_common_routes(
    common_routes: list[dict],
    route_stops_csv_path: str,
    timetable_csv_path: str,
) -> list[dict]:
    results = []

    directional_stops, _ = (
        load_directional_bus_indexes(
            route_stops_csv_path,
            timetable_csv_path,
        )
    )

    for route in common_routes:
        origin = route["origin_stop"]
        destination = route["destination_stop"]

        seconds, minutes = calculate_in_vehicle_time(
            origin,
            destination,
        )

        stop_count = (
            destination["direction_order"]
            - origin["direction_order"]
        )

        traversed_stops = [
            stop
            for stop in directional_stops.get(
                route["route_key"],
                [],
            )
            if (
                origin["direction_order"]
                <= stop["direction_order"]
                <= destination["direction_order"]
            )
        ]

        results.append({
            **route,
            "origin_order": origin["direction_order"],
            "destination_order": destination[
                "direction_order"
            ],
            "stop_count": stop_count,
            "intermediate_stop_count": max(
                stop_count - 1,
                0,
            ),
            "in_vehicle_seconds": seconds,
            "in_vehicle_minutes": minutes,
            "total_stop_count": stop_count,
            "total_in_vehicle_seconds": seconds,
            "total_in_vehicle_minutes": minutes,
            "origin_walking_distance_m": round(
                origin.get("distance_m", 0),
                1,
            ),
            "destination_walking_distance_m": round(
                destination.get("distance_m", 0),
                1,
            ),
            "total_walking_distance_m": round(
                origin.get("distance_m", 0)
                + destination.get("distance_m", 0),
                1,
            ),
            "traversed_stops": traversed_stops,
        })

    best_by_key = {}

    for route in results:
        key = route["route_key"]
        current = best_by_key.get(key)

        score = (
            route["total_in_vehicle_minutes"],
            route["total_walking_distance_m"],
        )

        if current is None or score < (
            current["total_in_vehicle_minutes"],
            current["total_walking_distance_m"],
        ):
            best_by_key[key] = route

    results = list(best_by_key.values())

    results.sort(
        key=lambda route: (
            route["total_in_vehicle_minutes"],
            route["total_walking_distance_m"],
        )
    )

    return results


def _group_routes_with_stops(
    stop_routes: list[dict],
) -> dict[tuple[str, str], dict]:
    grouped = {}

    for stop_data in stop_routes:
        for route in stop_data["routes"]:
            route_key = route["route_key"]

            grouped.setdefault(
                route_key,
                {
                    "route": {
                        key: value
                        for key, value in route.items()
                        if key != "matched_stop"
                    },
                    "stops": [],
                },
            )

            stop = route["matched_stop"]

            if not any(
                existing["node_id"] == stop["node_id"]
                and existing["direction_order"]
                == stop["direction_order"]
                for existing in grouped[
                    route_key
                ]["stops"]
            ):
                grouped[route_key]["stops"].append(
                    stop
                )

    return grouped


def find_one_transfer_routes(
    origin_stop_routes: list[dict],
    destination_stop_routes: list[dict],
    route_stops_csv_path: str,
    timetable_csv_path: str,
    max_transfer_walk_m: float,
    max_results: int = 100,
) -> list[dict]:
    """
    방향별 독립 노선을 기준으로 1회 환승 경로를 찾는다.

    방향 1의 종점 이후 방향 2 정류장으로 계속 타는 경로는
    같은 직행으로 처리되지 않는다.
    """
    directional_stops, _ = (
        load_directional_bus_indexes(
            route_stops_csv_path,
            timetable_csv_path,
        )
    )

    origin_groups = _group_routes_with_stops(
        origin_stop_routes
    )
    destination_groups = _group_routes_with_stops(
        destination_stop_routes
    )

    results = []
    seen = set()

    for first_key, first_data in origin_groups.items():
        first_stops = directional_stops[first_key]

        for second_key, second_data in (
            destination_groups.items()
        ):
            if first_key == second_key:
                continue

            second_stops = directional_stops[
                second_key
            ]

            for first_boarding in first_data["stops"]:
                for second_alighting in second_data[
                    "stops"
                ]:
                    for first_transfer in first_stops:
                        if (
                            first_transfer[
                                "direction_order"
                            ]
                            <= first_boarding[
                                "direction_order"
                            ]
                        ):
                            continue

                        for second_transfer in second_stops:
                            if (
                                second_transfer[
                                    "direction_order"
                                ]
                                >= second_alighting[
                                    "direction_order"
                                ]
                            ):
                                continue

                            transfer_distance = (
                                haversine_distance(
                                    first_transfer[
                                        "latitude"
                                    ],
                                    first_transfer[
                                        "longitude"
                                    ],
                                    second_transfer[
                                        "latitude"
                                    ],
                                    second_transfer[
                                        "longitude"
                                    ],
                                )
                            )

                            if (
                                transfer_distance
                                > max_transfer_walk_m
                            ):
                                continue

                            first_seconds, first_minutes = (
                                calculate_in_vehicle_time(
                                    first_boarding,
                                    first_transfer,
                                )
                            )

                            second_seconds, second_minutes = (
                                calculate_in_vehicle_time(
                                    second_transfer,
                                    second_alighting,
                                )
                            )

                            unique_key = (
                                first_key,
                                second_key,
                                first_boarding["node_id"],
                                first_boarding[
                                    "direction_order"
                                ],
                                first_transfer["node_id"],
                                first_transfer[
                                    "direction_order"
                                ],
                                second_transfer["node_id"],
                                second_transfer[
                                    "direction_order"
                                ],
                                second_alighting["node_id"],
                                second_alighting[
                                    "direction_order"
                                ],
                            )

                            if unique_key in seen:
                                continue

                            seen.add(unique_key)

                            first_count = (
                                first_transfer[
                                    "direction_order"
                                ]
                                - first_boarding[
                                    "direction_order"
                                ]
                            )

                            second_count = (
                                second_alighting[
                                    "direction_order"
                                ]
                                - second_transfer[
                                    "direction_order"
                                ]
                            )

                            total_seconds = (
                                first_seconds
                                + second_seconds
                            )

                            origin_walk = first_boarding.get(
                                "distance_m",
                                0,
                            )
                            destination_walk = (
                                second_alighting.get(
                                    "distance_m",
                                    0,
                                )
                            )

                            results.append({
                                "path_type": "transfer",
                                "transfer_count": 1,
                                "first_route": {
                                    **first_data["route"],
                                    "boarding_stop": (
                                        first_boarding
                                    ),
                                    "alighting_stop": (
                                        first_transfer
                                    ),
                                    "origin_order": (
                                        first_boarding[
                                            "direction_order"
                                        ]
                                    ),
                                    "destination_order": (
                                        first_transfer[
                                            "direction_order"
                                        ]
                                    ),
                                    "stop_count": first_count,
                                    "in_vehicle_seconds": (
                                        first_seconds
                                    ),
                                    "in_vehicle_minutes": (
                                        first_minutes
                                    ),
                                    "traversed_stops": [
                                        stop
                                        for stop in first_stops
                                        if (
                                            first_boarding[
                                                "direction_order"
                                            ]
                                            <= stop[
                                                "direction_order"
                                            ]
                                            <= first_transfer[
                                                "direction_order"
                                            ]
                                        )
                                    ],
                                },
                                "second_route": {
                                    **second_data["route"],
                                    "boarding_stop": (
                                        second_transfer
                                    ),
                                    "alighting_stop": (
                                        second_alighting
                                    ),
                                    "origin_order": (
                                        second_transfer[
                                            "direction_order"
                                        ]
                                    ),
                                    "destination_order": (
                                        second_alighting[
                                            "direction_order"
                                        ]
                                    ),
                                    "stop_count": second_count,
                                    "in_vehicle_seconds": (
                                        second_seconds
                                    ),
                                    "in_vehicle_minutes": (
                                        second_minutes
                                    ),
                                    "traversed_stops": [
                                        stop
                                        for stop in second_stops
                                        if (
                                            second_transfer[
                                                "direction_order"
                                            ]
                                            <= stop[
                                                "direction_order"
                                            ]
                                            <= second_alighting[
                                                "direction_order"
                                            ]
                                        )
                                    ],
                                },
                                "transfer_alighting_stop": (
                                    first_transfer
                                ),
                                "transfer_boarding_stop": (
                                    second_transfer
                                ),
                                "transfer_walking_distance_m": round(
                                    transfer_distance,
                                    1,
                                ),
                                "transfer_walking_minutes": round(
                                    transfer_distance / 75,
                                    2,
                                ),
                                "total_stop_count": (
                                    first_count
                                    + second_count
                                ),
                                "origin_walking_distance_m": round(
                                    origin_walk,
                                    1,
                                ),
                                "destination_walking_distance_m": round(
                                    destination_walk,
                                    1,
                                ),
                                "total_walking_distance_m": round(
                                    origin_walk
                                    + transfer_distance
                                    + destination_walk,
                                    1,
                                ),
                                "total_wait_minutes": None,
                                "total_in_vehicle_seconds": round(
                                    total_seconds,
                                    1,
                                ),
                                "total_in_vehicle_minutes": round(
                                    total_seconds / 60,
                                    2,
                                ),
                            })

    results.sort(
        key=lambda route: (
            route["total_in_vehicle_minutes"],
            route["transfer_walking_distance_m"],
            route["total_walking_distance_m"],
        )
    )

    return results[:max_results]
