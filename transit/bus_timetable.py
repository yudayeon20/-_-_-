
# X | None 애노테이션을 문자열로 지연 평가한다.
# 이 한 줄이 없으면 Python 3.9 에서 import 만으로 TypeError 가 난다.
from __future__ import annotations
import csv
import re

from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


SEOUL_TZ = ZoneInfo("Asia/Seoul")


def _read_csv(csv_path: str) -> list[dict]:
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(
            f"시간표 CSV 파일을 찾을 수 없습니다: {path}"
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
        f"시간표 CSV 인코딩을 확인할 수 없습니다: {path}"
    ) from last_error


def normalize_text(value: object) -> str:
    """기·종점명 비교용 정규화."""
    text = str(value or "").strip().lower()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[\s·ㆍ/,_\-]", "", text)
    return text


def parse_departure_time(value: object) -> tuple[int, int] | None:
    text = str(value or "").strip()

    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return hour, minute


def get_service_day(
    target_datetime: datetime,
    service_day_override: str | None = None,
) -> str:
    """
    월~금은 평일, 토요일은 토요일, 일요일은 휴일로 판정한다.

    법정공휴일 자동 판정은 하지 않으므로 공휴일에는
    config.yaml의 service_day_override를 '휴일'로 설정한다.
    """
    if service_day_override:
        return service_day_override

    weekday = target_datetime.weekday()

    if weekday == 5:
        return "토요일"

    if weekday == 6:
        return "휴일"

    return "평일"


def get_current_seoul_datetime() -> datetime:
    return datetime.now(SEOUL_TZ)


@lru_cache(maxsize=4)
def load_timetable(
    timetable_csv_path: str,
) -> tuple[dict, ...]:
    """
    시간표 CSV를 정규화해서 적재한다.

    필수 컬럼:
    route_id, route_no, route_type, service_day,
    direction_code, departure_terminal, arrival_terminal,
    departure_time
    """
    records = []

    for row in _read_csv(timetable_csv_path):
        route_id = str(row.get("route_id", "")).strip()
        direction_code = str(
            row.get("direction_code", "")
        ).strip()
        service_day = str(
            row.get("service_day", "")
        ).strip()

        parsed_time = parse_departure_time(
            row.get("departure_time", "")
        )

        if (
            not route_id
            or not direction_code
            or not service_day
            or parsed_time is None
        ):
            continue

        hour, minute = parsed_time

        records.append({
            "route_id": route_id,
            "route_no": str(
                row.get("route_no", "")
            ).strip(),
            "route_type": str(
                row.get("route_type", "")
            ).strip(),
            "service_day": service_day,
            "direction_code": direction_code,
            "departure_terminal": str(
                row.get("departure_terminal", "")
            ).strip(),
            "arrival_terminal": str(
                row.get("arrival_terminal", "")
            ).strip(),
            "departure_hour": hour,
            "departure_minute": minute,
            "departure_time": f"{hour:02d}:{minute:02d}",
        })

    records.sort(
        key=lambda row: (
            row["route_id"],
            row["direction_code"],
            row["service_day"],
            row["departure_hour"],
            row["departure_minute"],
        )
    )

    return tuple(records)


@lru_cache(maxsize=4)
def load_direction_definitions(
    timetable_csv_path: str,
) -> dict[str, tuple[dict, ...]]:
    """
    노선별 방향 정의를 반환한다.

    동일한 방향의 평일·토요일·휴일 행은 하나로 합친다.
    """
    definitions = defaultdict(dict)

    for row in load_timetable(timetable_csv_path):
        key = (
            row["direction_code"],
            normalize_text(row["departure_terminal"]),
            normalize_text(row["arrival_terminal"]),
        )

        definitions[row["route_id"]][key] = {
            "route_id": row["route_id"],
            "route_no": row["route_no"],
            "route_type": row["route_type"],
            "direction_code": row["direction_code"],
            "departure_terminal": row[
                "departure_terminal"
            ],
            "arrival_terminal": row[
                "arrival_terminal"
            ],
        }

    result = {}

    for route_id, items in definitions.items():
        values = list(items.values())

        values.sort(
            key=lambda item: (
                int(item["direction_code"])
                if item["direction_code"].isdigit()
                else 999,
                item["direction_code"],
            )
        )

        result[route_id] = tuple(values)

    return result


@lru_cache(maxsize=4)
def load_departure_index(
    timetable_csv_path: str,
) -> dict[tuple[str, str, str], tuple[dict, ...]]:
    index = defaultdict(list)

    for row in load_timetable(timetable_csv_path):
        key = (
            row["route_id"],
            row["direction_code"],
            row["service_day"],
        )
        index[key].append(row)

    return {
        key: tuple(value)
        for key, value in index.items()
    }


def find_upcoming_arrivals(
    timetable_csv_path: str,
    route_id: str,
    direction_code: str,
    boarding_direction_time_sec: float,
    alighting_direction_time_sec: float,
    query_datetime: datetime,
    count: int = 2,
    minimum_boarding_buffer_minutes: float = 0,
    max_search_days: int = 1,
    service_day_override: str | None = None,
    search_base_date: date | None = None,
) -> list[dict]:
    """
    현재시각 이후 탑승 정류장에 도착할 차량을 count대 찾는다.

    방향별 기점 출발시각
    + 방향 기점부터 정류장까지의 누적예상시간
    으로 탑승·하차 예상시각을 계산한다.

    조회 기준일에 차량이 부족하면 다음 날 첫 차량까지만
    추가하며, 그 이후 날짜는 조회하지 않는다.
    """
    if query_datetime.tzinfo is None:
        query_datetime = query_datetime.replace(
            tzinfo=SEOUL_TZ
        )
    else:
        query_datetime = query_datetime.astimezone(
            SEOUL_TZ
        )

    earliest_boarding = (
        query_datetime
        + timedelta(
            minutes=minimum_boarding_buffer_minutes
        )
    )
    base_date = search_base_date or query_datetime.date()
    last_search_date = base_date + timedelta(days=1)

    departure_index = load_departure_index(
        timetable_csv_path
    )

    results = []

    for day_offset in range(max_search_days + 1):
        target_date = (
            query_datetime
            + timedelta(days=day_offset)
        )
        if target_date.date() > last_search_date:
            break

        day_override = (
            service_day_override
            if day_offset == 0
            else None
        )

        service_day = get_service_day(
            target_date,
            service_day_override=day_override,
        )

        departures = departure_index.get(
            (
                str(route_id),
                str(direction_code),
                service_day,
            ),
            (),
        )

        for departure in departures:
            terminal_departure = target_date.replace(
                hour=departure["departure_hour"],
                minute=departure["departure_minute"],
                second=0,
                microsecond=0,
            )

            boarding_arrival = (
                terminal_departure
                + timedelta(
                    seconds=float(
                        boarding_direction_time_sec
                    )
                )
            )

            if boarding_arrival < earliest_boarding:
                continue

            alighting_arrival = (
                terminal_departure
                + timedelta(
                    seconds=float(
                        alighting_direction_time_sec
                    )
                )
            )

            wait_seconds = (
                boarding_arrival
                - query_datetime
            ).total_seconds()

            results.append({
                "route_id": route_id,
                "direction_code": direction_code,
                "service_day": service_day,
                "departure_terminal": departure[
                    "departure_terminal"
                ],
                "arrival_terminal": departure[
                    "arrival_terminal"
                ],
                "terminal_departure_datetime": (
                    terminal_departure
                ),
                "boarding_arrival_datetime": (
                    boarding_arrival
                ),
                "alighting_arrival_datetime": (
                    alighting_arrival
                ),
                "wait_seconds": round(
                    wait_seconds,
                    1,
                ),
                "wait_minutes": round(
                    wait_seconds / 60,
                    2,
                ),
                "day_offset": day_offset,
            })

            if target_date.date() > base_date:
                return results

            if len(results) >= count:
                return results

    return results
