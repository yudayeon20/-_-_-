
# 'X | None' 애노테이션을 문자열로 지연 평가한다.
# 이 줄이 없으면 Python 3.9 에서는 import 만으로 TypeError 가 난다.
from __future__ import annotations
import json
import os
import time
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from typing import Any

import requests

from bus_timetable import SEOUL_TZ


ARRIVAL_URL = (
    "https://apis.data.go.kr/1613000/"
    "ArvlInfoInqireService/"
    "getSttnAcctoSpcifyRouteBusArvlPrearngeInfoList"
)

STOP_ARRIVAL_URL = (
    "https://apis.data.go.kr/1613000/"
    "ArvlInfoInqireService/"
    "getSttnAcctoArvlPrearngeInfoList"
)

ROUTE_LOCATION_URL = (
    "https://apis.data.go.kr/1613000/"
    "BusLcInfoInqireService/"
    "getRouteAcctoBusLcList"
)

CHUNCHEON_BIS_ARRIVAL_URL = (
    "https://ccbus.chuncheon.go.kr/"
    "rest/api/v1/rbs/predict/arrival"
)


class TagoApiError(RuntimeError):
    """TAGO API 호출 또는 응답 해석 오류."""


def _as_list(value: Any) -> list:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def _to_int(
    value: Any,
    default: int | None = None,
) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(
    value: Any,
    default: float | None = None,
) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_xml_response(text: str) -> dict:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise TagoApiError(
            "TAGO API 응답이 JSON이나 XML 형식이 아닙니다."
        ) from error

    header = root.find(".//header")

    result_code = (
        header.findtext("resultCode")
        if header is not None
        else None
    )
    result_msg = (
        header.findtext("resultMsg")
        if header is not None
        else None
    )

    items = []

    for item_element in root.findall(".//item"):
        item = {}

        for child in item_element:
            item[child.tag] = child.text

        items.append(item)

    return {
        "result_code": result_code,
        "result_msg": result_msg,
        "items": items,
    }


def _parse_json_response(data: dict) -> dict:
    if not isinstance(data, dict):
        raise TagoApiError(
            "TAGO API JSON 응답의 최상위 형식이 올바르지 않습니다."
        )

    response = data.get("response", data)
    if not isinstance(response, dict):
        raise TagoApiError(
            "TAGO API JSON response 형식이 올바르지 않습니다."
        )

    header = response.get("header", {})
    body = response.get("body", {})
    if not isinstance(header, dict):
        header = {}
    if not isinstance(body, dict):
        body = {}
    items_container = body.get("items", {})

    if isinstance(items_container, dict):
        items = _as_list(
            items_container.get("item")
        )
    else:
        items = []

    return {
        "result_code": str(
            header.get("resultCode", "")
        ),
        "result_msg": str(
            header.get("resultMsg", "")
        ),
        "items": [
            item
            for item in items
            if isinstance(item, dict)
        ],
    }


def _request_tago(
    url: str,
    params: dict,
    timeout_seconds: float,
) -> dict:
    try:
        response = requests.get(
            url,
            params=params,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise TagoApiError(
            f"TAGO API 요청 실패: {error}"
        ) from error

    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    parsed = None

    if "json" in content_type:
        try:
            parsed = _parse_json_response(
                response.json()
            )
        except (
            ValueError,
            json.JSONDecodeError,
        ):
            parsed = None

    if parsed is None:
        stripped = response.text.lstrip()

        if stripped.startswith("{"):
            try:
                parsed = _parse_json_response(
                    response.json()
                )
            except (
                ValueError,
                json.JSONDecodeError,
            ):
                parsed = None

    if parsed is None:
        parsed = _parse_xml_response(
            response.text
        )

    result_code = str(
        parsed.get("result_code", "")
    ).strip()

    if result_code not in {"00", "0"}:
        raise TagoApiError(
            "TAGO API 오류 "
            f"[{result_code}]: "
            f"{parsed.get('result_msg', '')}"
        )

    return parsed


class TagoRealtimeClient:
    """
    한 번의 프로그램 실행 동안 동일한 정류장·노선 조회를 캐시한다.

    인증키는 코드나 YAML에 직접 적지 않고 환경변수에서 읽는다.
    """

    def __init__(
        self,
        city_code: str,
        service_key_env: str = (
            "DATA_GO_KR_SERVICE_KEY"
        ),
        timeout_seconds: float = 8,
        num_of_rows: int = 20,
        verify_route_location: bool = False,
    ) -> None:
        self.city_code = str(city_code).strip()
        self.service_key_env = (
            service_key_env
        )
        self.timeout_seconds = float(
            timeout_seconds
        )
        self.num_of_rows = int(
            num_of_rows
        )
        self.verify_route_location = bool(
            verify_route_location
        )

        self._arrival_cache = {}
        self._stop_arrival_cache = {}
        self._location_cache = {}

    @property
    def service_key(self) -> str | None:
        value = os.getenv(
            self.service_key_env
        )

        if value is None:
            return None

        value = value.strip()
        return value or None

    @property
    def is_configured(self) -> bool:
        return bool(
            self.city_code
            and self.service_key
        )

    def _base_params(self) -> dict:
        if not self.service_key:
            raise TagoApiError(
                "공공데이터포털 인증키 환경변수가 없습니다: "
                f"{self.service_key_env}"
            )

        return {
            "serviceKey": self.service_key,
            "pageNo": 1,
            "numOfRows": self.num_of_rows,
            "_type": "json",
            "cityCode": self.city_code,
        }

    def get_route_arrivals(
        self,
        node_id: str,
        route_id: str,
    ) -> list[dict]:
        """
        정류소별 특정노선 도착예정정보를 조회한다.

        arrtime은 현재 조회시각부터 해당 정류장까지 남은 초다.
        """
        cache_key = (
            str(node_id),
            str(route_id),
        )

        if cache_key in self._arrival_cache:
            return self._arrival_cache[
                cache_key
            ]

        params = {
            **self._base_params(),
            "nodeId": str(node_id),
            "routeId": str(route_id),
        }

        parsed = _request_tago(
            ARRIVAL_URL,
            params,
            self.timeout_seconds,
        )

        results = []

        for item in parsed["items"]:
            item_route_id = str(
                item.get("routeid", "")
            ).strip()

            if (
                item_route_id
                and item_route_id
                != str(route_id)
            ):
                continue

            arrival_seconds = _to_int(
                item.get("arrtime"),
                default=None,
            )

            if (
                arrival_seconds is None
                or arrival_seconds < 0
            ):
                continue

            results.append({
                "node_id": str(
                    item.get("nodeid", node_id)
                ).strip(),
                "node_name": str(
                    item.get("nodenm", "")
                ).strip(),
                "route_id": (
                    item_route_id
                    or str(route_id)
                ),
                "route_number": str(
                    item.get("routeno", "")
                ).strip(),
                "route_type": str(
                    item.get("routetp", "")
                ).strip(),
                "arrival_seconds": (
                    arrival_seconds
                ),
                "remaining_stop_count": _to_int(
                    item.get(
                        "arrprevstationcnt"
                    ),
                    default=None,
                ),
                "vehicle_type": str(
                    item.get("vehicletp", "")
                ).strip() or None,
            })

        results.sort(
            key=lambda item: (
                item["arrival_seconds"],
                item.get(
                    "remaining_stop_count"
                )
                if item.get(
                    "remaining_stop_count"
                )
                is not None
                else 9999,
            )
        )

        self._arrival_cache[
            cache_key
        ] = results

        return results

    def get_stop_arrivals(
        self,
        node_id: str,
    ) -> list[dict]:
        """정류소에 도착할 모든 노선의 실시간 정보를 조회한다."""
        node_id = str(node_id)
        if node_id in self._stop_arrival_cache:
            return self._stop_arrival_cache[node_id]

        params = {
            **self._base_params(),
            "nodeId": node_id,
        }
        parsed = _request_tago(
            STOP_ARRIVAL_URL,
            params,
            self.timeout_seconds,
        )

        results = []
        for item in parsed["items"]:
            arrival_seconds = _to_int(
                item.get("arrtime"),
                default=None,
            )
            if (
                arrival_seconds is None
                or arrival_seconds < 0
            ):
                continue

            results.append({
                "node_id": str(
                    item.get("nodeid", node_id)
                ).strip(),
                "node_name": str(
                    item.get("nodenm", "")
                ).strip(),
                "route_id": str(
                    item.get("routeid", "")
                ).strip(),
                "route_number": str(
                    item.get("routeno", "")
                ).strip(),
                "route_type": str(
                    item.get("routetp", "")
                ).strip(),
                "arrival_seconds": arrival_seconds,
                "remaining_stop_count": _to_int(
                    item.get("arrprevstationcnt"),
                    default=None,
                ),
                "vehicle_type": str(
                    item.get("vehicletp", "")
                ).strip() or None,
            })

        results.sort(
            key=lambda item: item["arrival_seconds"]
        )
        self._stop_arrival_cache[node_id] = results
        return results

    def get_route_locations(
        self,
        route_id: str,
    ) -> list[dict]:
        """
        선택적으로 노선별 GPS 위치 목록을 조회한다.

        도착시간 자체는 도착정보 API의 arrtime을 사용하고,
        이 호출은 현재 노선에서 GPS 위치정보가 실제 제공되는지
        확인하는 보조 용도다.
        """
        route_id = str(route_id)

        if route_id in self._location_cache:
            return self._location_cache[
                route_id
            ]

        params = {
            **self._base_params(),
            "routeId": route_id,
        }

        parsed = _request_tago(
            ROUTE_LOCATION_URL,
            params,
            self.timeout_seconds,
        )

        results = []

        for item in parsed["items"]:
            latitude = _to_float(
                item.get("gpslati"),
                default=None,
            )
            longitude = _to_float(
                item.get("gpslong"),
                default=None,
            )

            results.append({
                "route_number": str(
                    item.get("routenm", "")
                ).strip(),
                "latitude": latitude,
                "longitude": longitude,
                "node_order": _to_int(
                    item.get("nodeord"),
                    default=None,
                ),
                "node_name": str(
                    item.get("nodenm", "")
                ).strip(),
                "node_id": str(
                    item.get("nodeid", "")
                ).strip(),
                "route_type": str(
                    item.get("routetp", "")
                ).strip(),
                "vehicle_number": str(
                    item.get("vehicleno", "")
                ).strip() or None,
            })

        self._location_cache[
            route_id
        ] = results

        return results

    def build_realtime_predictions(
        self,
        node_id: str,
        route_id: str,
        query_datetime: datetime,
        in_vehicle_seconds: float,
        count: int = 2,
        earliest_boarding_datetime: (
            datetime | None
        ) = None,
    ) -> dict:
        """
        실시간 API 결과를 서비스 출력용으로 변환한다.

        탑승 정류장 도착시각:
          TAGO arrtime을 그대로 사용

        하차 예상시각:
          실시간 탑승 정류장 도착시각
          + 자체 계산한 구간 탑승시간

        따라서 하차시각은 '순수 GPS 실측값'이 아니라
        '실시간 도착정보 + 구간 예상 이동시간'이다.
        """
        if query_datetime.tzinfo is None:
            query_datetime = (
                query_datetime.replace(
                    tzinfo=SEOUL_TZ
                )
            )
        else:
            query_datetime = (
                query_datetime.astimezone(
                    SEOUL_TZ
                )
            )

        if (
            earliest_boarding_datetime
            is not None
            and earliest_boarding_datetime.tzinfo
            is None
        ):
            earliest_boarding_datetime = (
                earliest_boarding_datetime.replace(
                    tzinfo=SEOUL_TZ
                )
            )

        arrivals = self.get_route_arrivals(
            node_id=node_id,
            route_id=route_id,
        )

        gps_locations = []

        if self.verify_route_location:
            try:
                gps_locations = (
                    self.get_route_locations(
                        route_id
                    )
                )
            except TagoApiError:
                gps_locations = []

        predictions = []

        for item in arrivals:
            boarding_arrival = (
                query_datetime
                + timedelta(
                    seconds=item[
                        "arrival_seconds"
                    ]
                )
            )

            if (
                earliest_boarding_datetime
                is not None
                and boarding_arrival
                < earliest_boarding_datetime
            ):
                continue

            alighting_estimate = (
                boarding_arrival
                + timedelta(
                    seconds=float(
                        in_vehicle_seconds
                    )
                )
            )

            predictions.append({
                **item,
                "source": (
                    "TAGO_REALTIME_ARRIVAL_API"
                ),
                "query_datetime": (
                    query_datetime
                ),
                "boarding_arrival_datetime": (
                    boarding_arrival
                ),
                "alighting_estimated_datetime": (
                    alighting_estimate
                ),
                "gps_location_available": bool(
                    gps_locations
                ),
            })

            if len(predictions) >= count:
                break

        return {
            "available": bool(predictions),
            "predictions": predictions,
            "gps_location_available": bool(
                gps_locations
            ),
            "raw_arrival_count": len(arrivals),
        }


class ChuncheonBisRealtimeClient(
    TagoRealtimeClient
):
    """춘천·홍천축 BIS의 공개 정류장 도착예측 정보를 사용한다."""

    def __init__(
        self,
        timeout_seconds: float = 8,
        cache_ttl_seconds: float = 30,
    ) -> None:
        super().__init__(
            city_code="",
            timeout_seconds=timeout_seconds,
            num_of_rows=100,
            verify_route_location=False,
        )
        self.cache_ttl_seconds = max(
            float(cache_ttl_seconds),
            0,
        )
        self._arrival_cache_times = {}

    @property
    def is_configured(self) -> bool:
        return True

    def get_route_arrivals(
        self,
        node_id: str,
        route_id: str,
    ) -> list[dict]:
        cache_key = (
            str(node_id),
            str(route_id),
        )
        cached_at = self._arrival_cache_times.get(
            cache_key
        )
        if (
            cache_key in self._arrival_cache
            and cached_at is not None
            and time.monotonic() - cached_at
            <= self.cache_ttl_seconds
        ):
            return self._arrival_cache[cache_key]

        try:
            response = requests.get(
                CHUNCHEON_BIS_ARRIVAL_URL,
                params={
                    "entity.stationId": str(node_id),
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (
            requests.RequestException,
            ValueError,
        ) as error:
            raise TagoApiError(
                f"춘천 BIS 요청 실패: {error}"
            ) from error

        if not isinstance(payload, list):
            raise TagoApiError(
                "춘천 BIS 도착예측 응답 형식이 올바르지 않습니다."
            )

        results = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            item_route_id = str(
                item.get("entityId", "")
            ).strip()
            if item_route_id != str(route_id):
                continue

            predict_minute = _to_float(
                item.get("predictMinute"),
                default=None,
            )
            if (
                predict_minute is None
                or predict_minute < 0
            ):
                continue

            results.append({
                "node_id": str(node_id),
                "node_name": str(
                    item.get("stationName", "")
                ).strip(),
                "route_id": item_route_id,
                "route_number": str(
                    item.get("routeName", "")
                ).strip(),
                "route_type": str(
                    item.get("routeTypeName", "")
                ).strip(),
                "arrival_seconds": round(
                    predict_minute * 60
                ),
                "remaining_stop_count": _to_int(
                    item.get("leftStationCount"),
                    default=None,
                ),
                "vehicle_type": (
                    str(
                        item.get(
                            "cityRouteTypeCode",
                            "",
                        )
                    ).strip()
                    or None
                ),
                "vehicle_number": (
                    str(
                        item.get("plateNumber", "")
                    ).strip()
                    or None
                ),
                "destination_name": (
                    str(
                        item.get(
                            "finalArrivalStation",
                            "",
                        )
                    ).strip()
                    or None
                ),
                "source": "CHUNCHEON_BIS",
            })

        results.sort(
            key=lambda item: item["arrival_seconds"]
        )
        self._arrival_cache[cache_key] = results
        self._arrival_cache_times[
            cache_key
        ] = time.monotonic()
        return results
