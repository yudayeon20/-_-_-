
# X | None 애노테이션을 문자열로 지연 평가한다.
# 이 한 줄이 없으면 Python 3.9 에서 import 만으로 TypeError 가 난다.
from __future__ import annotations
import pprint

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from bus_stop import get_candidate_stops
from bus_route import (
    analyze_common_routes,
    find_common_routes,
    find_one_transfer_routes,
    get_routes_for_stops,
)
from bus_timetable import (
    SEOUL_TZ,
    find_upcoming_arrivals,
    get_current_seoul_datetime,
)
from bus_realtime import (
    ChuncheonBisRealtimeClient,
    TagoApiError,
    TagoRealtimeClient,
)


def format_duration_seconds(
    value: float | None,
) -> str:
    if value is None:
        return "계산 전"

    total_seconds = max(round(value), 0)
    hours, remainder = divmod(
        total_seconds,
        3600,
    )
    minutes, seconds = divmod(
        remainder,
        60,
    )

    parts = []

    if hours:
        parts.append(f"{hours}시간")

    if minutes:
        parts.append(f"{minutes}분")

    if seconds or not parts:
        parts.append(f"{seconds}초")

    return "약 " + " ".join(parts)


def format_datetime_korean(
    value: datetime,
) -> str:
    return value.strftime(
        "%m월 %d일 %H:%M:%S"
    )


def build_direct_route_results(
    routes: list[dict],
) -> list[dict]:
    results = []

    for route in routes:
        results.append({
            "rank": None,
            "path_type": "direct",
            "transfer_count": 0,
            "schedule_available": False,
            "total_wait_minutes": None,
            "total_elapsed_minutes": None,
            "total_in_vehicle_minutes": route[
                "total_in_vehicle_minutes"
            ],
            "total_in_vehicle_seconds": route[
                "total_in_vehicle_seconds"
            ],
            "total_stop_count": route[
                "total_stop_count"
            ],
            "origin_walking_distance_m": route[
                "origin_walking_distance_m"
            ],
            "destination_walking_distance_m": route[
                "destination_walking_distance_m"
            ],
            "total_walking_distance_m": route[
                "total_walking_distance_m"
            ],
            "segments": [{
                "segment_number": 1,
                "route_key": route["route_key"],
                "route_id": route["route_id"],
                "route_number": route[
                    "route_number"
                ],
                "route_type": route.get(
                    "route_type"
                ),
                "direction_code": route[
                    "direction_code"
                ],
                "departure_terminal": route[
                    "departure_terminal"
                ],
                "arrival_terminal": route[
                    "arrival_terminal"
                ],
                "boarding_stop": route[
                    "origin_stop"
                ],
                "alighting_stop": route[
                    "destination_stop"
                ],
                "transfer_stop": None,
                "origin_order": route[
                    "origin_order"
                ],
                "destination_order": route[
                    "destination_order"
                ],
                "stop_count": route[
                    "stop_count"
                ],
                "intermediate_stop_count": route[
                    "intermediate_stop_count"
                ],
                "wait_minutes": None,
                "in_vehicle_seconds": route[
                    "in_vehicle_seconds"
                ],
                "in_vehicle_minutes": route[
                    "in_vehicle_minutes"
                ],
                "traversed_stops": route.get(
                    "traversed_stops",
                    [],
                ),
                "upcoming_arrivals": [],
                "realtime_arrivals": [],
                "realtime_status": "not_queried",
                "realtime_error": None,
            }],
        })

    return results


def build_transfer_route_results(
    routes: list[dict],
) -> list[dict]:
    results = []

    for route in routes:
        first = route["first_route"]
        second = route["second_route"]

        results.append({
            "rank": None,
            "path_type": "transfer",
            "transfer_count": 1,
            "schedule_available": False,
            "total_wait_minutes": None,
            "total_elapsed_minutes": None,
            "total_in_vehicle_seconds": route[
                "total_in_vehicle_seconds"
            ],
            "total_in_vehicle_minutes": route[
                "total_in_vehicle_minutes"
            ],
            "total_stop_count": route[
                "total_stop_count"
            ],
            "origin_walking_distance_m": route[
                "origin_walking_distance_m"
            ],
            "destination_walking_distance_m": route[
                "destination_walking_distance_m"
            ],
            "total_walking_distance_m": route[
                "total_walking_distance_m"
            ],
            "transfer_walking_distance_m": route[
                "transfer_walking_distance_m"
            ],
            "transfer_walking_minutes": route[
                "transfer_walking_minutes"
            ],
            "transfer_alighting_stop": route[
                "transfer_alighting_stop"
            ],
            "transfer_boarding_stop": route[
                "transfer_boarding_stop"
            ],
            "segments": [
                {
                    "segment_number": 1,
                    "route_key": first["route_key"],
                    "route_id": first["route_id"],
                    "route_number": first[
                        "route_number"
                    ],
                    "route_type": first.get(
                        "route_type"
                    ),
                    "direction_code": first[
                        "direction_code"
                    ],
                    "departure_terminal": first[
                        "departure_terminal"
                    ],
                    "arrival_terminal": first[
                        "arrival_terminal"
                    ],
                    "boarding_stop": first[
                        "boarding_stop"
                    ],
                    "alighting_stop": first[
                        "alighting_stop"
                    ],
                    "transfer_stop": route[
                        "transfer_alighting_stop"
                    ],
                    "origin_order": first[
                        "origin_order"
                    ],
                    "destination_order": first[
                        "destination_order"
                    ],
                    "stop_count": first[
                        "stop_count"
                    ],
                    "intermediate_stop_count": max(
                        first["stop_count"] - 1,
                        0,
                    ),
                    "wait_minutes": None,
                    "in_vehicle_seconds": first[
                        "in_vehicle_seconds"
                    ],
                    "in_vehicle_minutes": first[
                        "in_vehicle_minutes"
                    ],
                    "traversed_stops": first.get(
                        "traversed_stops",
                        [],
                    ),
                    "upcoming_arrivals": [],
                "realtime_arrivals": [],
                "realtime_status": "not_queried",
                "realtime_error": None,
                },
                {
                    "segment_number": 2,
                    "route_key": second[
                        "route_key"
                    ],
                    "route_id": second["route_id"],
                    "route_number": second[
                        "route_number"
                    ],
                    "route_type": second.get(
                        "route_type"
                    ),
                    "direction_code": second[
                        "direction_code"
                    ],
                    "departure_terminal": second[
                        "departure_terminal"
                    ],
                    "arrival_terminal": second[
                        "arrival_terminal"
                    ],
                    "boarding_stop": second[
                        "boarding_stop"
                    ],
                    "alighting_stop": second[
                        "alighting_stop"
                    ],
                    "transfer_stop": None,
                    "origin_order": second[
                        "origin_order"
                    ],
                    "destination_order": second[
                        "destination_order"
                    ],
                    "stop_count": second[
                        "stop_count"
                    ],
                    "intermediate_stop_count": max(
                        second["stop_count"] - 1,
                        0,
                    ),
                    "wait_minutes": None,
                    "in_vehicle_seconds": second[
                        "in_vehicle_seconds"
                    ],
                    "in_vehicle_minutes": second[
                        "in_vehicle_minutes"
                    ],
                    "traversed_stops": second.get(
                        "traversed_stops",
                        [],
                    ),
                    "upcoming_arrivals": [],
                "realtime_arrivals": [],
                "realtime_status": "not_queried",
                "realtime_error": None,
                },
            ],
        })

    return results


def combine_route_results(
    direct_results: list[dict],
    transfer_results: list[dict],
    max_in_vehicle_minutes: float,
) -> list[dict]:
    results = direct_results + transfer_results

    results = [
        result
        for result in results
        if (
            result[
                "total_in_vehicle_minutes"
            ]
            <= max_in_vehicle_minutes
        )
    ]

    results.sort(
        key=lambda result: (
            result["total_in_vehicle_minutes"],
            result["transfer_count"],
            result["total_walking_distance_m"],
            result["total_stop_count"],
        )
    )

    for rank, result in enumerate(
        results,
        start=1,
    ):
        result["rank"] = rank

    return results


def apply_timetable_predictions(
    route_results: list[dict],
    timetable_csv_path: str,
    query_datetime: datetime,
    upcoming_arrival_count: int,
    minimum_boarding_buffer_minutes: float,
    service_day_override: str | None,
    walking_speed_m_per_minute: float = 75,
    transfer_buffer_minutes: float = 2,
) -> list[dict]:
    """
    직행·환승 경로에 시간표 기반 도착예측을 붙인다.

    환승 경로의 두 번째 버스는:
    첫 번째 버스 하차시각 + 환승 도보시간 이후 차량을 조회한다.
    """
    for result in route_results:
        segment_query_datetime = query_datetime
        total_wait_seconds = 0
        schedule_available = True
        origin_walking_minutes = (
            result["origin_walking_distance_m"]
            / walking_speed_m_per_minute
        )
        result["origin_walking_minutes"] = round(
            origin_walking_minutes,
            2,
        )
        result["destination_walking_minutes"] = round(
            result["destination_walking_distance_m"]
            / walking_speed_m_per_minute,
            2,
        )

        for segment in result["segments"]:
            boarding = segment["boarding_stop"]
            alighting = segment[
                "alighting_stop"
            ]

            arrivals = find_upcoming_arrivals(
                timetable_csv_path=(
                    timetable_csv_path
                ),
                route_id=segment["route_id"],
                direction_code=segment[
                    "direction_code"
                ],
                boarding_direction_time_sec=(
                    boarding[
                        "direction_cumulative_time_sec"
                    ]
                ),
                alighting_direction_time_sec=(
                    alighting[
                        "direction_cumulative_time_sec"
                    ]
                ),
                query_datetime=(
                    segment_query_datetime
                ),
                count=upcoming_arrival_count,
                minimum_boarding_buffer_minutes=(
                    (
                        minimum_boarding_buffer_minutes
                        + origin_walking_minutes
                    )
                    if segment["segment_number"] == 1
                    else 0
                ),
                service_day_override=(
                    service_day_override
                    if segment[
                        "segment_number"
                    ] == 1
                    else None
                ),
                search_base_date=query_datetime.date(),
            )

            segment["upcoming_arrivals"] = arrivals

            if not arrivals:
                segment["schedule_available"] = False
                segment["schedule_error"] = (
                    "현재 이후 이용 가능한 시간표를 찾지 못했습니다."
                )
                schedule_available = False
                # 앞 구간 시간표가 없어도 실시간 차량으로 탑승할 수 있다.
                # 다음 환승 구간의 시간표 fallback까지 계속 준비한다.
                segment_query_datetime += timedelta(
                    seconds=segment[
                        "in_vehicle_seconds"
                    ]
                )
                if (
                    result["path_type"] == "transfer"
                    and segment["segment_number"] == 1
                ):
                    segment_query_datetime += timedelta(
                        minutes=(
                            result[
                                "transfer_walking_minutes"
                            ]
                            + transfer_buffer_minutes
                        )
                    )
                continue

            selected = arrivals[0]

            segment["schedule_available"] = True
            segment["schedule_error"] = None
            segment["selected_arrival"] = selected
            segment["wait_minutes"] = selected[
                "wait_minutes"
            ]
            segment["boarding_arrival_time"] = (
                selected[
                    "boarding_arrival_datetime"
                ]
            )
            segment["alighting_arrival_time"] = (
                selected[
                    "alighting_arrival_datetime"
                ]
            )

            total_wait_seconds += selected[
                "wait_seconds"
            ]

            segment_query_datetime = selected[
                "alighting_arrival_datetime"
            ]

            if (
                result["path_type"] == "transfer"
                and segment["segment_number"] == 1
            ):
                segment_query_datetime += timedelta(
                    minutes=(
                        result["transfer_walking_minutes"]
                        + transfer_buffer_minutes
                    )
                )

        result["schedule_available"] = (
            schedule_available
        )

        if schedule_available:
            final_arrival = result["segments"][
                -1
            ]["selected_arrival"][
                "alighting_arrival_datetime"
            ]

            result["predicted_final_arrival_time"] = (
                final_arrival
            )
            result[
                "predicted_destination_arrival_time"
            ] = (
                final_arrival
                + timedelta(
                    minutes=result[
                        "destination_walking_minutes"
                    ]
                )
            )
            result["total_wait_minutes"] = round(
                total_wait_seconds / 60,
                2,
            )
            result["total_elapsed_minutes"] = round(
                (
                    result[
                        "predicted_destination_arrival_time"
                    ]
                    - query_datetime
                ).total_seconds()
                / 60,
                2,
            )
            if result["path_type"] == "transfer":
                first_alighting = result["segments"][0][
                    "alighting_arrival_time"
                ]
                second_boarding = result["segments"][1][
                    "boarding_arrival_time"
                ]
                result["transfer_slack_minutes"] = round(
                    (
                        second_boarding
                        - first_alighting
                    ).total_seconds()
                    / 60
                    - result["transfer_walking_minutes"],
                    2,
                )
            else:
                result["transfer_slack_minutes"] = None
        else:
            result["predicted_final_arrival_time"] = (
                None
            )
            result[
                "predicted_destination_arrival_time"
            ] = None
            result["transfer_slack_minutes"] = None

    return route_results


def _path_signature(result: dict) -> tuple:
    """버스 번호와 무관한 실제 이동 정류장 순서를 식별한다."""
    return (
        result["path_type"],
        tuple(
            tuple(
                stop["node_id"]
                for stop in segment.get(
                    "traversed_stops",
                    [],
                )
            )
            for segment in result["segments"]
        ),
    )


def _route_option(segment: dict) -> dict:
    option = {
        "route_id": segment["route_id"],
        "route_number": segment["route_number"],
        "direction_code": segment["direction_code"],
        "departure_terminal": segment[
            "departure_terminal"
        ],
        "arrival_terminal": segment[
            "arrival_terminal"
        ],
    }
    selected = segment.get("selected_arrival")
    if selected:
        option.update({
            "terminal_departure_datetime": (
                selected.get(
                    "terminal_departure_datetime"
                )
            ),
            "boarding_arrival_datetime": (
                selected.get(
                    "boarding_arrival_datetime"
                )
            ),
            "alighting_arrival_datetime": (
                selected.get(
                    "alighting_arrival_datetime"
                )
                or selected.get(
                    "alighting_estimated_datetime"
                )
            ),
        })
    return option


def group_equivalent_routes(
    route_results: list[dict],
) -> list[dict]:
    """
    지나가는 정류장 순서가 같은 경로를 카드 하나로 묶는다.

    가장 빨리 목적지에 도착하는 운행을 대표 경로로 사용하고,
    함께 탈 수 있는 버스 번호는 구간별 선택지로 보존한다.
    """
    grouped = {}

    for result in route_results:
        if not result.get("schedule_available"):
            continue

        signature = _path_signature(result)
        grouped.setdefault(signature, []).append(result)

    results = []

    for variants in grouped.values():
        variants.sort(
            key=lambda item: item[
                "predicted_destination_arrival_time"
            ]
        )
        representative = deepcopy(variants[0])

        for segment_index, segment in enumerate(
            representative["segments"]
        ):
            options = {}

            for variant in variants:
                option = _route_option(
                    variant["segments"][segment_index]
                )
                key = (
                    option["route_id"],
                    option["direction_code"],
                )
                options[key] = option

            segment["route_options"] = list(
                options.values()
            )
            segment["route_number_options"] = list(
                dict.fromkeys(
                    option["route_number"]
                    for option in options.values()
                )
            )

        representative["grouped_route_count"] = len(
            variants
        )
        representative["route_variants"] = [
            [
                _route_option(segment)
                for segment in variant["segments"]
            ]
            for variant in variants
        ]
        results.append(representative)

    return results


def deduplicate_overlapping_bus_paths(
    route_results: list[dict],
) -> list[dict]:
    """
    같은 버스 조합으로 이동할 수 있고 정류장만 다른 후보를 합친다.

    출발·환승·도착 도보를 모두 합친 총 도보거리가 가장 짧은
    경로를 대표로 남긴다.
    """
    ordered = sorted(
        route_results,
        key=lambda result: (
            result["predicted_destination_arrival_time"],
            (
                result.get("transfer_slack_minutes")
                is not None
                and result["transfer_slack_minutes"] < 2
            ),
            result.get("total_wait_minutes")
            if result.get("total_wait_minutes") is not None
            else float("inf"),
            result["total_walking_distance_m"],
            result["total_in_vehicle_minutes"],
        ),
    )
    selected = []
    seen_transfer_combinations = set()

    def route_sets(result: dict) -> list[set[str]]:
        return [
            {
                option["route_id"]
                for option in segment.get(
                    "route_options",
                    [],
                )
            }
            or {segment["route_id"]}
            for segment in result["segments"]
        ]

    for result in ordered:
        current_sets = route_sets(result)

        if (
            result["path_type"] == "transfer"
            and len(result["segments"]) == 2
        ):
            first, second = result["segments"]
            combination_key = (
                first["boarding_stop"]["node_id"],
                first["route_id"],
                second["route_id"],
                second["alighting_stop"]["node_id"],
            )
            if combination_key in seen_transfer_combinations:
                continue
            seen_transfer_combinations.add(
                combination_key
            )

        overlaps_existing = False

        for existing in selected:
            if (
                result["path_type"]
                != existing["path_type"]
                or len(result["segments"])
                != len(existing["segments"])
            ):
                continue

            existing_sets = route_sets(existing)
            result_segments = result["segments"]
            existing_segments = existing["segments"]
            same_except_final_alighting = all(
                current["boarding_stop"]["node_id"]
                == previous["boarding_stop"]["node_id"]
                and (
                    index == len(result_segments) - 1
                    or current["alighting_stop"]["node_id"]
                    == previous["alighting_stop"]["node_id"]
                )
                for index, (current, previous) in enumerate(
                    zip(result_segments, existing_segments)
                )
            )
            same_except_first_boarding = all(
                current["alighting_stop"]["node_id"]
                == previous["alighting_stop"]["node_id"]
                and (
                    index == 0
                    or current["boarding_stop"]["node_id"]
                    == previous["boarding_stop"]["node_id"]
                )
                for index, (current, previous) in enumerate(
                    zip(result_segments, existing_segments)
                )
            )
            shares_bus_each_segment = all(
                current & previous
                for current, previous in zip(
                    current_sets,
                    existing_sets,
                )
            )
            if (
                shares_bus_each_segment
                and (
                    same_except_final_alighting
                    or same_except_first_boarding
                )
            ):
                overlaps_existing = True
                break

        if not overlaps_existing:
            selected.append(result)

    return selected


def rank_and_select_route_results(
    route_results: list[dict],
    query_datetime: datetime,
    max_results_per_category: int = 6,
    arrival_tie_minutes: float = 5,
) -> list[dict]:
    """
    전체 유효 후보를 유지한 채 카테고리별 상위 경로를 고른다.

    추천순은 오늘 도착 여부를 가장 먼저 고려한다. 나머지
    카테고리는 각 카테고리의 핵심 기준을 먼저 적용한다.
    """
    grouped = deduplicate_overlapping_bus_paths(
        group_equivalent_routes(route_results)
    )
    for result in grouped:
        result["category_ranks"] = {}
        result["display_categories"] = []

    today = query_datetime.date()
    pool = [
        result
        for result in grouped
        if (
            result.get(
                "predicted_destination_arrival_time"
            )
            is not None
            and result[
                "predicted_destination_arrival_time"
            ] > query_datetime
        )
    ]

    if not pool:
        return []

    tie_seconds = max(arrival_tie_minutes, 0) * 60

    def recommendation_key(result: dict) -> tuple:
        arrival = result[
            "predicted_destination_arrival_time"
        ]
        arrives_today = arrival.date() == today
        same_day_arrivals = [
            item["predicted_destination_arrival_time"]
            for item in pool
            if (
                item["predicted_destination_arrival_time"].date()
                == arrival.date()
            )
        ]
        earliest_for_day = min(same_day_arrivals)
        arrival_group = int(
            max(
                (
                    arrival
                    - earliest_for_day
                ).total_seconds(),
                0,
            )
            // max(tie_seconds, 1)
        )
        transfer_slack = result.get(
            "transfer_slack_minutes"
        )
        return (
            not arrives_today,
            arrival_group,
            result["transfer_count"],
            result["total_walking_distance_m"],
            -(
                transfer_slack
                if transfer_slack is not None
                else 10**9
            ),
            result["total_in_vehicle_minutes"],
            arrival,
        )

    category_keys = {
        "recommended": recommendation_key,
        "fastest_arrival": lambda result: (
            result[
                "predicted_destination_arrival_time"
            ],
        ),
        "direct_first": lambda result: (
            result["transfer_count"],
            result[
                "predicted_destination_arrival_time"
            ],
        ),
        "least_walking": lambda result: (
            result["total_walking_distance_m"],
            result[
                "predicted_destination_arrival_time"
            ],
        ),
    }

    def category_pool(category: str) -> list[dict]:
        if category == "direct_first":
            return [
                result
                for result in pool
                if result["transfer_count"] == 0
            ]
        return pool

    for category, key_function in category_keys.items():
        for category_rank, result in enumerate(
            sorted(
                category_pool(category),
                key=key_function,
            ),
            start=1,
        ):
            result.setdefault(
                "category_ranks",
                {},
            )[category] = category_rank

    minimum_walk = min(
        result["total_walking_distance_m"]
        for result in pool
    )
    minimum_ride = min(
        result["total_in_vehicle_minutes"]
        for result in pool
    )

    for result in pool:
        tags = []
        if (
            result[
                "predicted_destination_arrival_time"
            ].date()
            == today
        ):
            tags.append("#오늘도착")
        else:
            tags.append("#내일첫차")
        if (
            result["category_ranks"][
                "fastest_arrival"
            ]
            == 1
        ):
            tags.append("#가장빠름")
        if result["transfer_count"] == 0:
            tags.append("#직통")
        else:
            tags.append("#환승1회")
            if (
                result.get("transfer_slack_minutes")
                is not None
                and result["transfer_slack_minutes"] >= 2
            ):
                tags.append("#환승여유")
        if (
            result["total_walking_distance_m"]
            == minimum_walk
        ):
            tags.append("#최소도보")
        if (
            result["total_in_vehicle_minutes"]
            == minimum_ride
        ):
            tags.append("#최소탑승시간")
        result["tags"] = tags

    category_results = {}
    for category, key_function in category_keys.items():
        category_results[category] = sorted(
            category_pool(category),
            key=key_function,
        )[:max_results_per_category]

    selected = []
    selected_by_signature = {}
    for category in category_keys:
        for result in category_results[category]:
            signature = _path_signature(result)
            selected_result = selected_by_signature.get(
                signature
            )
            if selected_result is None:
                selected_result = result
                selected_result[
                    "display_categories"
                ] = []
                selected_by_signature[
                    signature
                ] = selected_result
                selected.append(selected_result)

            selected_result["display_categories"].append(
                category
            )

    for rank, result in enumerate(selected, start=1):
        result["rank"] = rank

    return selected



def apply_realtime_predictions(
    route_results: list[dict],
    realtime_client: TagoRealtimeClient,
    query_datetime: datetime,
    realtime_arrival_count: int,
    max_routes_to_enrich: int,
) -> list[dict]:
    """
    상위 경로의 각 버스 구간에 TAGO 실시간 도착정보를 붙인다.

    실시간 API가 응답하지 않거나 해당 노선의 정보가 없으면
    기존 시간표 기반 예측을 그대로 유지한다.
    """
    if not realtime_client.is_configured:
        for result in route_results:
            for segment in result["segments"]:
                segment[
                    "realtime_status"
                ] = "disabled"
                segment[
                    "realtime_error"
                ] = (
                    "인증키 또는 도시코드가 설정되지 않았습니다."
                )
                segment[
                    "realtime_arrivals"
                ] = []

        return route_results

    # 표시 가능성이 높은 후보만 대상으로 중복 요청을 제거한 뒤 병렬 조회한다.
    # 이후 build_realtime_predictions는 클라이언트 캐시를 사용하므로 추가 호출이 없다.
    lookup_pairs = list(dict.fromkeys(
        (
            str(segment["boarding_stop"]["node_id"]),
            str(segment["route_id"]),
        )
        for result in route_results[:max_routes_to_enrich]
        for segment in result["segments"]
    ))
    if lookup_pairs:
        with ThreadPoolExecutor(
            max_workers=min(8, len(lookup_pairs))
        ) as executor:
            futures = [
                executor.submit(
                    realtime_client.get_route_arrivals,
                    node_id,
                    route_id,
                )
                for node_id, route_id in lookup_pairs
            ]
            for future in futures:
                try:
                    future.result()
                except TagoApiError:
                    # 아래 구간별 처리에서 오류 상태와 메시지를 기록한다.
                    pass

    for result_index, result in enumerate(
        route_results
    ):
        if result_index >= max_routes_to_enrich:
            for segment in result["segments"]:
                segment[
                    "realtime_status"
                ] = "not_queried"
                segment[
                    "realtime_error"
                ] = (
                    "API 호출량 제한을 위해 조회하지 않았습니다."
                )
                segment[
                    "realtime_arrivals"
                ] = []

            continue

        transfer_ready_datetime = None

        for segment in result["segments"]:
            boarding = segment["boarding_stop"]

            try:
                realtime = (
                    realtime_client
                    .build_realtime_predictions(
                        node_id=boarding["node_id"],
                        route_id=segment["route_id"],
                        query_datetime=query_datetime,
                        in_vehicle_seconds=segment[
                            "in_vehicle_seconds"
                        ],
                        count=realtime_arrival_count,
                        earliest_boarding_datetime=(
                            transfer_ready_datetime
                        ),
                    )
                )

                segment[
                    "realtime_arrivals"
                ] = realtime[
                    "predictions"
                ]
                segment[
                    "gps_location_available"
                ] = realtime[
                    "gps_location_available"
                ]

                if realtime["available"]:
                    segment[
                        "realtime_status"
                    ] = "available"
                    segment[
                        "realtime_error"
                    ] = None

                    selected = realtime[
                        "predictions"
                    ][0]

                    if (
                        result["path_type"]
                        == "transfer"
                        and segment[
                            "segment_number"
                        ] == 1
                    ):
                        transfer_ready_datetime = (
                            selected[
                                "alighting_estimated_datetime"
                            ]
                            + timedelta(
                                minutes=result[
                                    "transfer_walking_minutes"
                                ]
                            )
                        )
                else:
                    segment[
                        "realtime_status"
                    ] = "no_active_bus"
                    segment[
                        "realtime_error"
                    ] = (
                        "현재 운행 중인 실시간 도착정보가 없습니다."
                    )

                    # 첫 구간 실시간 정보가 없으면
                    # 두 번째 구간의 탑승 가능시각은
                    # 시간표 예측값으로 이어 간다.
                    if (
                        result["path_type"]
                        == "transfer"
                        and segment[
                            "segment_number"
                        ] == 1
                        and segment.get(
                            "selected_arrival"
                        )
                    ):
                        transfer_ready_datetime = (
                            segment[
                                "selected_arrival"
                            ][
                                "alighting_arrival_datetime"
                            ]
                            + timedelta(
                                minutes=result[
                                    "transfer_walking_minutes"
                                ]
                            )
                        )

            except TagoApiError as error:
                segment[
                    "realtime_status"
                ] = "error"
                segment[
                    "realtime_error"
                ] = str(error)
                segment[
                    "realtime_arrivals"
                ] = []

    return route_results


def recalculate_realtime_route_results(
    route_results: list[dict],
    query_datetime: datetime,
    transfer_buffer_minutes: float = 2,
) -> list[dict]:
    """실시간 도착정보를 기준으로 경로 시간과 환승 가능 여부를 다시 계산한다."""
    for result in route_results:
        ready_datetime = (
            query_datetime
            + timedelta(
                minutes=result.get(
                    "origin_walking_minutes",
                    0,
                )
            )
        )
        total_wait_seconds = 0
        route_available = True

        for segment in result["segments"]:
            candidates = segment.get(
                "realtime_arrivals",
                [],
            )
            source = "realtime"
            selected = next(
                (
                    arrival
                    for arrival in candidates
                    if arrival[
                        "boarding_arrival_datetime"
                    ] >= ready_datetime
                ),
                None,
            )

            if selected is None:
                source = "timetable"
                selected = next(
                    (
                        arrival
                        for arrival in segment.get(
                            "upcoming_arrivals",
                            [],
                        )
                        if arrival[
                            "boarding_arrival_datetime"
                        ] >= ready_datetime
                    ),
                    None,
                )

            if selected is None:
                route_available = False
                break

            boarding_datetime = selected[
                "boarding_arrival_datetime"
            ]
            if source == "realtime":
                alighting_datetime = selected[
                    "alighting_estimated_datetime"
                ]
            else:
                alighting_datetime = selected[
                    "alighting_arrival_datetime"
                ]

            segment["selected_arrival"] = selected
            segment["prediction_source"] = source
            segment["boarding_arrival_time"] = (
                boarding_datetime
            )
            segment["alighting_arrival_time"] = (
                alighting_datetime
            )
            wait_seconds = max(
                (
                    boarding_datetime
                    - ready_datetime
                ).total_seconds(),
                0,
            )
            segment["wait_minutes"] = round(
                wait_seconds / 60,
                2,
            )
            total_wait_seconds += wait_seconds

            ready_datetime = alighting_datetime
            if (
                result["path_type"] == "transfer"
                and segment["segment_number"] == 1
            ):
                ready_datetime += timedelta(
                    minutes=(
                        result["transfer_walking_minutes"]
                        + transfer_buffer_minutes
                    )
                )

        if not route_available:
            result["schedule_available"] = False
            result["predicted_final_arrival_time"] = None
            result[
                "predicted_destination_arrival_time"
            ] = None
            result["transfer_slack_minutes"] = None
            continue

        # 실시간 정보만 있고 시간표가 없는 경로도 최종 후보로 유지한다.
        result["schedule_available"] = True
        final_arrival = result["segments"][-1][
            "alighting_arrival_time"
        ]
        destination_arrival = (
            final_arrival
            + timedelta(
                minutes=result[
                    "destination_walking_minutes"
                ]
            )
        )
        result["predicted_final_arrival_time"] = final_arrival
        result[
            "predicted_destination_arrival_time"
        ] = destination_arrival
        result["total_wait_minutes"] = round(
            total_wait_seconds / 60,
            2,
        )
        result["total_elapsed_minutes"] = round(
            (
                destination_arrival
                - query_datetime
            ).total_seconds()
            / 60,
            2,
        )

        if result["path_type"] == "transfer":
            result["transfer_slack_minutes"] = round(
                (
                    result["segments"][1][
                        "boarding_arrival_time"
                    ]
                    - result["segments"][0][
                        "alighting_arrival_time"
                    ]
                ).total_seconds()
                / 60
                - result["transfer_walking_minutes"],
                2,
            )
        else:
            result["transfer_slack_minutes"] = None

    return route_results


def print_route_results(
    results: list[dict],
    query_datetime: datetime,
) -> None:
    print("버스 경로 및 도착예정정보")
    print("=" * 72)
    print(
        "조회 기준시각: "
        + query_datetime.strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    )

    if not results:
        print(
            "조건을 만족하는 버스 경로를 찾지 못했습니다."
        )
        return

    for result in results:
        print(
            f"\n{result['rank']}. "
            f"경로 유형: {result['path_type']}"
        )
        if result.get("tags"):
            print(
                "   추천 태그: "
                + " ".join(result["tags"])
            )
        if result.get("display_categories"):
            print(
                "   표시 카테고리: "
                + ", ".join(
                    result["display_categories"]
                )
            )
        print(
            f"   총 환승 횟수: "
            f"{result['transfer_count']}회"
        )
        print(
            "   총 버스 탑승시간: "
            + format_duration_seconds(
                result[
                    "total_in_vehicle_seconds"
                ]
            )
        )
        print(
            f"   총 정류장 구간 수: "
            f"{result['total_stop_count']}개"
        )
        print(
            f"   총 도보거리: "
            f"{result['total_walking_distance_m']}m"
        )
        print(
            f"   출발지 → 첫 탑승 정류장 거리: "
            f"{result['origin_walking_distance_m']}m"
        )
        print(
            f"   마지막 하차 정류장 → 목적지 거리: "
            f"{result['destination_walking_distance_m']}m"
        )

        if result["schedule_available"]:
            print(
                "   최종 도착예정: "
                + format_datetime_korean(
                    result[
                        "predicted_destination_arrival_time"
                    ]
                )
            )
            print(
                "   조회시각부터 최종 도착까지: "
                + format_duration_seconds(
                    result[
                        "total_elapsed_minutes"
                    ]
                    * 60
                )
            )
        else:
            print(
                "   시간표 기반 도착예측: 계산 불가"
            )

        if result["path_type"] == "transfer":
            print(
                f"   환승 도보거리: "
                f"{result['transfer_walking_distance_m']}m"
            )

        for segment in result["segments"]:
            boarding = segment["boarding_stop"]
            alighting = segment["alighting_stop"]

            print(
                f"   [{segment['segment_number']}구간]"
            )
            print(
                f"      탑승 버스: "
                f"{segment['route_number']}번"
            )
            route_number_options = segment.get(
                "route_number_options",
                [],
            )
            if len(route_number_options) > 1:
                print(
                    "      같은 경로 버스: "
                    + ", ".join(route_number_options)
                    + "번"
                )
            print(
                f"      운행 방향: "
                f"{segment['departure_terminal']} "
                f"→ {segment['arrival_terminal']} "
                f"(방향 {segment['direction_code']})"
            )
            print(
                f"      탑승 정류장: "
                f"{boarding['node_name']} "
                f"({boarding.get('node_number')})"
            )
            print(
                f"      하차 정류장: "
                f"{alighting['node_name']} "
                f"({alighting.get('node_number')})"
            )
            print(
                "      버스 탑승시간: "
                + format_duration_seconds(
                    segment["in_vehicle_seconds"]
                )
            )

            traversed_stops = segment.get(
                "traversed_stops",
                [],
            )

            print(
                f"      이동 정거장 수: "
                f"{segment['stop_count']}개"
            )
            print(
                f"      정차 정류장 수"
                f"(탑승·하차 포함): "
                f"{len(traversed_stops)}곳"
            )

            if traversed_stops:
                print(
                    "      지나가는 정류장:"
                )

                for stop_index, stop in enumerate(
                    traversed_stops,
                    start=1,
                ):
                    label = ""

                    if stop_index == 1:
                        label = " [탑승]"
                    elif stop_index == len(
                        traversed_stops
                    ):
                        label = " [하차]"

                    node_number = stop.get(
                        "node_number"
                    )
                    number_text = (
                        f" ({node_number})"
                        if node_number
                        else ""
                    )

                    print(
                        f"         {stop_index}. "
                        f"{stop['node_name']}"
                        f"{number_text}"
                        f"{label}"
                    )

            realtime_arrivals = segment.get(
                "realtime_arrivals",
                [],
            )

            # 실시간 정보가 있으면 실시간 정보만 출력한다.
            if realtime_arrivals:
                print(
                    "      [실시간 도착정보 API 기준]"
                )

                for index, arrival in enumerate(
                    realtime_arrivals,
                    start=1,
                ):
                    print(
                        f"         {index}) 탑승 정류장 "
                        + format_duration_seconds(
                            arrival[
                                "arrival_seconds"
                            ]
                        )
                        + " 뒤 도착 예정"
                    )

                    remaining_stop_count = (
                        arrival.get(
                            "remaining_stop_count"
                        )
                    )

                    if (
                        remaining_stop_count
                        is not None
                    ):
                        print(
                            "            남은 정류장 수: "
                            f"{remaining_stop_count}개"
                        )

                    print(
                        "            탑승 정류장 도착: "
                        + format_datetime_korean(
                            arrival[
                                "boarding_arrival_datetime"
                            ]
                        )
                    )
                    print(
                        "            하차 예상: "
                        + format_datetime_korean(
                            arrival[
                                "alighting_estimated_datetime"
                            ]
                        )
                        + " "
                        "(실시간 탑승 ETA + "
                        "구간 예상 이동시간)"
                    )

                continue

            # 실시간 정보가 없을 때만 시간표 기반 예상을 출력한다.
            print(
                "      [시간표 기반 예상]"
            )

            arrivals = segment[
                "upcoming_arrivals"
            ]

            if not arrivals:
                print(
                    "         "
                    + segment.get(
                        "schedule_error",
                        segment.get(
                            "realtime_error",
                            "계산 불가",
                        ),
                    )
                )
                continue

            print(
                f"         다음 버스 "
                f"{len(arrivals)}대:"
            )

            for index, arrival in enumerate(
                arrivals,
                start=1,
            ):
                next_day_text = ""

                if arrival["day_offset"] > 0:
                    next_day_text = (
                        f" / {arrival['service_day']} 운행"
                    )

                print(
                    f"         {index}) "
                    + format_duration_seconds(
                        arrival["wait_seconds"]
                    )
                    + " 뒤 탑승 정류장 도착"
                    + next_day_text
                )
                print(
                    "            기점 출발: "
                    + format_datetime_korean(
                        arrival[
                            "terminal_departure_datetime"
                        ]
                    )
                )
                print(
                    "            탑승 정류장 도착: "
                    + format_datetime_korean(
                        arrival[
                            "boarding_arrival_datetime"
                        ]
                    )
                )
                print(
                    "            하차 예정: "
                    + format_datetime_korean(
                        arrival[
                            "alighting_arrival_datetime"
                        ]
                    )
                )


def save_route_results_to_txt(
    results: list[dict],
    query_datetime: datetime,
    file_path: str,
) -> None:
    with open(
        file_path,
        "w",
        encoding="utf-8",
    ) as output_file:
        with redirect_stdout(output_file):
            print_route_results(
                results,
                query_datetime,
            )

            print("\n")
            print("전체 결과 dict")
            print("=" * 72)

            pprint.pp(
                results,
                sort_dicts=False,
                width=120,
            )


def resolve_query_datetime(
    config: dict,
) -> datetime:
    time_config = config.get("time", {})

    if time_config.get(
        "use_current_time",
        True,
    ):
        return get_current_seoul_datetime()

    test_text = time_config.get(
        "test_datetime"
    )

    if not test_text:
        raise ValueError(
            "use_current_time이 false이면 "
            "test_datetime을 입력해야 합니다."
        )

    parsed = datetime.strptime(
        test_text,
        "%Y-%m-%d %H:%M:%S",
    )

    return parsed.replace(tzinfo=SEOUL_TZ)


def main() -> None:
    transit_dir = Path(__file__).resolve().parent

    load_dotenv(
        transit_dir / ".env",
        override=False,
    )

    with (transit_dir / "config.yaml").open(
        "r",
        encoding="utf-8",
    ) as file:
        config = yaml.safe_load(file)

    route_stops_csv_path = config[
        "data"
    ]["route_stops_csv"]

    timetable_csv_path = config[
        "data"
    ]["timetable_csv"]

    query_datetime = resolve_query_datetime(
        config
    )

    origin_stops = get_candidate_stops(
        csv_path=route_stops_csv_path,
        latitude=config["test"]["origin"][
            "latitude"
        ],
        longitude=config["test"]["origin"][
            "longitude"
        ],
        max_distance_m=config["routing"][
            "max_stop_distance_m"
        ],
        max_candidates=config["routing"][
            "max_stop_candidates"
        ],
    )

    destination_stops = get_candidate_stops(
        csv_path=route_stops_csv_path,
        latitude=config["test"][
            "destination"
        ]["latitude"],
        longitude=config["test"][
            "destination"
        ]["longitude"],
        max_distance_m=config["routing"][
            "max_stop_distance_m"
        ],
        max_candidates=config["routing"][
            "max_stop_candidates"
        ],
    )

    origin_stop_routes = get_routes_for_stops(
        stops=origin_stops,
        route_stops_csv_path=(
            route_stops_csv_path
        ),
        timetable_csv_path=timetable_csv_path,
    )

    destination_stop_routes = (
        get_routes_for_stops(
            stops=destination_stops,
            route_stops_csv_path=(
                route_stops_csv_path
            ),
            timetable_csv_path=(
                timetable_csv_path
            ),
        )
    )

    common_routes = find_common_routes(
        origin_stop_routes,
        destination_stop_routes,
    )

    direct_routes = analyze_common_routes(
        common_routes=common_routes,
        route_stops_csv_path=(
            route_stops_csv_path
        ),
        timetable_csv_path=timetable_csv_path,
    )

    transfer_routes = find_one_transfer_routes(
        origin_stop_routes=origin_stop_routes,
        destination_stop_routes=(
            destination_stop_routes
        ),
        route_stops_csv_path=(
            route_stops_csv_path
        ),
        timetable_csv_path=timetable_csv_path,
        max_transfer_walk_m=config[
            "routing"
        ]["max_transfer_walk_m"],
        max_results=config["routing"].get(
            "max_transfer_results",
            100,
        ),
    )

    direct_results = build_direct_route_results(
        direct_routes
    )
    transfer_results = (
        build_transfer_route_results(
            transfer_routes
        )
    )

    all_results = combine_route_results(
        direct_results=direct_results,
        transfer_results=transfer_results,
        max_in_vehicle_minutes=config[
            "routing"
        ]["max_in_vehicle_minutes"],
    )

    all_results = apply_timetable_predictions(
        route_results=all_results,
        timetable_csv_path=timetable_csv_path,
        query_datetime=query_datetime,
        upcoming_arrival_count=config[
            "time"
        ].get("upcoming_arrival_count", 2),
        minimum_boarding_buffer_minutes=(
            config["time"].get(
                "minimum_boarding_buffer_minutes",
                0,
            )
        ),
        service_day_override=config[
            "time"
        ].get("service_day_override"),
        walking_speed_m_per_minute=config[
            "routing"
        ].get("walking_speed_m_per_minute", 75),
        transfer_buffer_minutes=config[
            "routing"
        ].get("transfer_buffer_minutes", 2),
    )

    all_results = rank_and_select_route_results(
        route_results=all_results,
        query_datetime=query_datetime,
        max_results_per_category=config["output"].get(
            "max_results_per_category",
            3,
        ),
        arrival_tie_minutes=config["routing"].get(
            "arrival_tie_minutes",
            5,
        ),
    )

    realtime_config = config.get(
        "realtime",
        {},
    )

    realtime_client = ChuncheonBisRealtimeClient(
        timeout_seconds=realtime_config.get(
            "timeout_seconds",
            8,
        ),
    )

    all_results = apply_realtime_predictions(
        route_results=all_results,
        realtime_client=realtime_client,
        query_datetime=query_datetime,
        realtime_arrival_count=(
            realtime_config.get(
                "arrival_count",
                2,
            )
        ),
        max_routes_to_enrich=(
            realtime_config.get(
                "max_routes_to_enrich",
                20,
            )
        ),
    )

    all_results = recalculate_realtime_route_results(
        route_results=all_results,
        query_datetime=query_datetime,
        transfer_buffer_minutes=config["routing"].get(
            "transfer_buffer_minutes",
            2,
        ),
    )

    all_results = rank_and_select_route_results(
        route_results=all_results,
        query_datetime=query_datetime,
        max_results_per_category=config["output"].get(
            "max_results_per_category",
            3,
        ),
        arrival_tie_minutes=config["routing"].get(
            "arrival_tie_minutes",
            5,
        ),
    )

    output_file_path = config.get(
        "output",
        {},
    ).get(
        "result_txt",
        "route_results.txt",
    )

    save_route_results_to_txt(
        all_results,
        query_datetime,
        output_file_path,
    )

    print_route_results(
        all_results,
        query_datetime,
    )

    print(
        f"\n경로 결과 저장 완료: "
        f"{output_file_path} "
        f"({len(all_results)}개 경로)"
    )


if __name__ == "__main__":
    main()
