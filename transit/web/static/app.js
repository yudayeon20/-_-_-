const state = {
  data: null,
  category: "recommended",
  receivedAt: null,
};

const $ = (selector) => document.querySelector(selector);
const routeList = $("#route-list");
const loading = $("#loading");
const errorBox = $("#error");

function compactNumbers(numbers) {
  if (numbers.length <= 2) return numbers.join(", ");
  return `${numbers.slice(0, 2).join(", ")} 외 ${numbers.length - 2}개`;
}

function shortBusNumber(number) {
  const text = String(number).trim();
  const qualifierIndex = text.indexOf("(");
  return qualifierIndex > 0
    ? text.slice(0, qualifierIndex).trim()
    : text;
}

function busQualifier(number) {
  const match = String(number).match(/\(([^)]+)\)/);
  if (!match) return "";
  return match[1].replace(/경유$/, " 경유");
}

function realtimeWaitText(seconds, remainingStopCount) {
  const safeSeconds = Math.max(Math.ceil(seconds), 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  const timeText = minutes
    ? `${minutes}분 ${remainder}초 후`
    : `${remainder}초 후`;
  const stopText =
    remainingStopCount != null
      ? ` (${remainingStopCount}개 전)`
      : "";
  return `${timeText}${stopText}`;
}

function routeNumbers(route, compact = true) {
  return route.segments
    .map((segment) =>
      compact
        ? compactNumbers(segment.route_numbers)
        : segment.route_numbers.join(", ")
    )
    .join(" → ");
}

function createRouteCard(route, index) {
  const fragment = $("#route-template").content.cloneNode(true);
  const card = fragment.querySelector(".route-card");
  card.querySelector(".rank").textContent = index + 1;
  card.querySelector(".total-time").textContent = route.in_vehicle_text;
  card.querySelector(".route-type").textContent =
    route.transfer_count ? "환승 1회 경로" : "직통 경로";
  card.querySelector(".arrival strong").textContent = route.arrival_time || "시간 확인 중";
  card.querySelector(".arrival span").textContent = `버스 탑승 ${route.in_vehicle_text}`;

  const tags = card.querySelector(".tags");
  route.tags.forEach((text) => {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = text;
    tags.appendChild(tag);
  });

  const walkBreakdown = card.querySelector(".walk-breakdown");
  const walkItems = [
    ["출발 도보", route.origin_walking_distance_m],
    ...(route.transfer_count
      ? [["환승 도보", route.transfer_walking_distance_m]]
      : []),
    ["도착 도보", route.destination_walking_distance_m],
  ];
  walkBreakdown.innerHTML = walkItems
    .map(
      ([label, distance]) =>
        `<span><small>${label}</small><strong>${Math.round(distance)}m</strong></span>`
    )
    .join("");

  const journey = card.querySelector(".journey");
  route.segments.forEach((segment) => {
    const row = document.createElement("div");
    row.className = "segment";
    const primaryBus = shortBusNumber(segment.route_numbers[0]);
    const alternatives =
      segment.route_numbers.length > 1
        ? ` · 같은 경로 ${segment.route_numbers.length}대`
        : "";
    const primaryOption = segment.bus_options[0];
    const realtime = segment.realtime_arrivals[0];
    const realtimeStops =
      realtime?.remaining_stop_count != null
        ? ` (${realtime.remaining_stop_count}개 전)`
        : "";
    const arrivalText = realtime
      ? `<span class="realtime-arrival" data-arrival-seconds="${realtime.arrival_seconds}" data-stop-text="${realtimeStops}"> · ${realtimeWaitText(
          realtime.arrival_seconds,
          realtime.remaining_stop_count
        )}</span>`
      : primaryOption?.wait_text
        ? ` · ${primaryOption.wait_text}`
        : "";
    row.innerHTML = `
      <span class="segment-bus" title="${segment.route_numbers[0]}">${primaryBus}</span>
      <span class="segment-copy">
        <strong>${segment.boarding_stop} → ${segment.alighting_stop}</strong>
        <small>${compactNumbers(segment.route_numbers)}번${arrivalText}</small>
        <small>${segment.stop_count}개 정류장 · 탑승 ${segment.in_vehicle_text}${alternatives}</small>
      </span>`;
    journey.appendChild(row);
  });

  card.querySelector(".ride-time").textContent = route.in_vehicle_text;
  card.querySelector(".walk-distance").textContent =
    `${Math.round(route.walking_distance_m)}m`;
  card.querySelector(".transfer-count").textContent =
    route.transfer_count ? "1회" : "없음";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `버스 탑승 ${route.in_vehicle_text} 경로 상세 보기`);
  card.addEventListener("click", () => openRouteDetail(route));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openRouteDetail(route);
    }
  });
  return fragment;
}

function timelineNode(type, title, subtitle = "") {
  const row = document.createElement("section");
  row.className = `timeline-node ${type}`;
  row.innerHTML = `
    <span class="timeline-icon"></span>
    <div class="timeline-copy">
      <strong>${title}</strong>
      ${subtitle ? `<small>${subtitle}</small>` : ""}
    </div>`;
  return row;
}

function walkingNode(distance, minutes, label = "도보") {
  return timelineNode(
    "walking",
    `${label} ${Math.round(distance)}m`,
    `${Math.max(Math.round(minutes), 1)}분 예상`
  );
}

function busNode(segment) {
  const row = timelineNode(
    "bus-leg",
    `${segment.boarding_stop} ${segment.boarding_stop_number || ""}`,
    `${segment.alighting_stop}에서 하차`
  );
  const copy = row.querySelector(".timeline-copy");
  const options = document.createElement("div");
  options.className = "bus-options";
  const busOptions = segment.bus_options.length
    ? segment.bus_options
    : segment.route_numbers.map((route_number) => ({ route_number }));

  const appendOption = (container, option, realtime = null) => {
    const item = document.createElement("div");
    if (realtime) item.classList.add("realtime-option");
    const qualifier = busQualifier(option.route_number);
    item.innerHTML = `
      <b title="${option.route_number}">${shortBusNumber(option.route_number)}</b>
      <span>${option.wait_text || option.boarding_time || "도착정보 없음"}</span>
      ${
        qualifier
          ? `<small class="route-qualifier">${qualifier}</small>`
          : ""
      }`;
    if (realtime) {
      const wait = item.querySelector("span");
      wait.classList.add("realtime-arrival");
      wait.dataset.arrivalSeconds = realtime.arrival_seconds;
      wait.dataset.stopText =
        realtime.remaining_stop_count != null
          ? ` (${realtime.remaining_stop_count}개 전)`
          : "";
    }
    container.appendChild(item);
  };
  const realtime = segment.realtime_arrivals[0];
  if (realtime) {
    appendOption(
      options,
      {
        route_number: segment.route_numbers[0],
        wait_text: realtimeWaitText(
          realtime.arrival_seconds,
          realtime.remaining_stop_count
        ),
      },
      realtime
    );
  } else {
    busOptions.slice(0, 3).forEach((option) => {
      appendOption(options, option);
    });
  }
  if (!realtime && busOptions.length > 3) {
    const more = document.createElement("details");
    more.className = "bus-option-more";
    more.innerHTML = `<summary>같은 경로 버스 ${busOptions.length - 3}대 더 보기</summary>`;
    busOptions.slice(3).forEach((option) => {
      appendOption(more, option);
    });
    options.appendChild(more);
  }
  copy.appendChild(options);

  const alighting = document.createElement("div");
  alighting.className = "alighting-summary";
  alighting.innerHTML = `
    <span>하차</span>
    <strong>${segment.alighting_stop}</strong>
    ${
      segment.alighting_stop_number
        ? `<small>${segment.alighting_stop_number}</small>`
        : ""
    }`;
  copy.appendChild(alighting);

  const details = document.createElement("details");
  details.className = "stop-details";
  const intermediateCount = Math.max(segment.stops.length - 2, 0);
  details.innerHTML = `
    <summary>${segment.stops.length}개 정류장 · ${segment.in_vehicle_text}
      <span>${intermediateCount ? "자세히 보기" : ""}</span>
    </summary>
    <ol>${segment.stops
      .map(
        (stop, index) =>
          `<li class="${index === 0 || index === segment.stops.length - 1 ? "terminal" : ""}">
            <span>${stop.name}</span>
            ${stop.number ? `<small>${stop.number}</small>` : ""}
          </li>`
      )
      .join("")}</ol>`;
  copy.appendChild(details);
  return row;
}

function openRouteDetail(route) {
  const dialog = $("#route-detail");
  $("#detail-total-time").textContent = route.in_vehicle_text;
  $("#detail-arrival").textContent = `${route.arrival_time} 도착 예정`;
  const tags = $("#detail-tags");
  tags.innerHTML = route.tags.map((tag) => `<span class="tag">${tag}</span>`).join("");

  const timeline = $("#detail-timeline");
  timeline.innerHTML = "";
  timeline.appendChild(
    timelineNode("start", state.data.origin.name, state.data.origin.description)
  );
  timeline.appendChild(
    walkingNode(route.origin_walking_distance_m, route.origin_walking_minutes)
  );

  route.segments.forEach((segment, index) => {
    timeline.appendChild(busNode(segment));
    if (index < route.segments.length - 1) {
      timeline.appendChild(
        walkingNode(
          route.transfer_walking_distance_m,
          route.transfer_walking_minutes,
          "환승 도보"
        )
      );
    }
  });

  timeline.appendChild(
    walkingNode(
      route.destination_walking_distance_m,
      route.destination_walking_minutes
    )
  );
  timeline.appendChild(
    timelineNode(
      "destination",
      state.data.destination.name,
      state.data.destination.description
    )
  );
  dialog.showModal();
}

function render() {
  routeList.innerHTML = "";
  if (!state.data) return;
  const category = state.data.categories[state.category];
  const routes = category?.routes || [];

  if (!routes.length) {
    errorBox.textContent = "이 조건에 맞는 버스 경로가 없어요.";
    errorBox.classList.remove("hidden");
    routeList.classList.add("hidden");
    return;
  }

  errorBox.classList.add("hidden");
  routes.forEach((route, index) => {
    routeList.appendChild(createRouteCard(route, index));
  });
  routeList.classList.remove("hidden");
}

function updateRealtimeCountdowns() {
  if (!state.receivedAt) return;
  const elapsedSeconds = Math.floor(
    (Date.now() - state.receivedAt) / 1000
  );
  document
    .querySelectorAll(".realtime-arrival[data-arrival-seconds]")
    .forEach((element) => {
      const initialSeconds = Number(
        element.dataset.arrivalSeconds
      );
      const remainingSeconds = Math.max(
        initialSeconds - elapsedSeconds,
        0
      );
      const stopText = element.dataset.stopText || "";
      const minutes = Math.floor(remainingSeconds / 60);
      const seconds = remainingSeconds % 60;
      const timeText = minutes
        ? `${minutes}분 ${seconds}초 후`
        : `${seconds}초 후`;
      const prefix =
        element.closest(".segment-copy") ? " · " : "";
      element.textContent = `${prefix}${timeText}${stopText}`;
    });
}

async function searchRoutes() {
  loading.classList.remove("hidden");
  routeList.classList.add("hidden");
  errorBox.classList.add("hidden");
  $("#search").disabled = true;

  try {
    const response = await fetch("/api/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        origin: await resolveOriginValue(),
        destination: $("#destination").value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "경로 조회 실패");
    state.data = payload;
    state.receivedAt = Date.now();
    $("#queried-at").textContent = `${payload.queried_at} 기준`;
    render();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  } finally {
    loading.classList.add("hidden");
    $("#search").disabled = false;
  }
}


// ---------------------------------------------------------------- 현재 위치
//
// 브라우저 Geolocation 은 두 가지를 조심해야 한다.
//   1) https 또는 localhost 에서만 동작한다. 팀원 PC IP 로 접속하면 막힌다.
//   2) 실내 wifi 측위는 오차가 수백 m ~ 수 km 다. 정류장 탐색 반경이 1km 라
//      오차가 그보다 크면 엉뚱한 정류장이 잡힌다. 그래서 정확도를 같이 보고
//      나쁘면 사용자에게 장소를 직접 고르라고 안내한다.
const GEO_ACCURACY_WARN_M = 500;

const geo = {
  coords: null,      // { latitude, longitude, accuracy_m }
  state: "idle",     // idle | loading | ok | denied | error | unsupported
  message: "",
};

function setGeoStatus(state, message) {
  geo.state = state;
  geo.message = message;
  const node = $("#geo-status");
  if (!node) return;
  node.textContent = message;
  node.className = "geo-status";
  if (state === "ok") node.classList.add("ok");
  else if (state === "error" || state === "denied") node.classList.add("error");
  else if (state === "warn") node.classList.add("warn");
}

function requestCurrentLocation({ silent = false } = {}) {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator)) {
      setGeoStatus("unsupported", "이 브라우저는 위치 기능을 지원하지 않아요. 장소를 직접 골라 주세요.");
      return resolve(null);
    }
    if (!window.isSecureContext) {
      setGeoStatus("error", "https 또는 localhost 에서만 현재 위치를 쓸 수 있어요.");
      return resolve(null);
    }
    if (!silent) setGeoStatus("loading", "위치 확인 중…");

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude, accuracy } = position.coords;
        geo.coords = {
          latitude,
          longitude,
          accuracy_m: Math.round(accuracy),
          name: "현재 위치",
        };
        if (accuracy > GEO_ACCURACY_WARN_M) {
          setGeoStatus("warn",
            `현재 위치 확인 (오차 약 ${Math.round(accuracy)}m) · 실내라면 부정확할 수 있어요`);
        } else {
          setGeoStatus("ok", `현재 위치 확인 (오차 약 ${Math.round(accuracy)}m)`);
        }
        resolve(geo.coords);
      },
      (error) => {
        geo.coords = null;
        const reason = {
          1: "위치 권한이 거부됐어요. 주소창 자물쇠 아이콘에서 허용해 주세요.",
          2: "위치를 가져오지 못했어요. 잠시 후 다시 시도해 주세요.",
          3: "위치 확인이 오래 걸려요. 장소를 직접 골라 주세요.",
        }[error.code] || "위치를 가져오지 못했어요.";
        setGeoStatus(error.code === 1 ? "denied" : "error", reason);
        resolve(null);
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 }
    );
  });
}

// 출발지 값 → 서버에 보낼 형태.
// 장소 key 는 문자열 그대로, 현재 위치는 좌표 객체로 보낸다.
async function resolveOriginValue() {
  const value = $("#origin").value;
  if (value !== "__current__") return value;

  const coords = geo.coords || (await requestCurrentLocation());
  if (!coords) {
    throw new Error(geo.message || "현재 위치를 확인하지 못했어요. 장소를 직접 골라 주세요.");
  }
  return coords;
}

document.querySelectorAll(".category-bar button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".category-bar button").forEach(
      (item) => item.classList.remove("active")
    );
    button.classList.add("active");
    state.category = button.dataset.category;
    render();
  });
});

$("#origin").addEventListener("change", (event) => {
  if (event.target.value === "__current__") {
    requestCurrentLocation();
  } else {
    setGeoStatus("idle", "");
  }
});

$("#swap").addEventListener("click", () => {
  const origin = $("#origin");
  const destination = $("#destination");
  // '현재 위치'는 도착지가 될 수 없다 (도착지는 프로그램 장소여야 한다)
  if (origin.value === "__current__") return;
  [origin.value, destination.value] = [destination.value, origin.value];
});
$(".detail-close").addEventListener("click", () => {
  $("#route-detail").close();
});
$("#route-detail").addEventListener("click", (event) => {
  if (event.target === $("#route-detail")) $("#route-detail").close();
});
$("#search").addEventListener("click", searchRoutes);
$("#back-to-map").addEventListener("click", () => {
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  window.location.href = "/";
});
(async () => {
  if ($("#origin").value === "__current__") await requestCurrentLocation();
  searchRoutes();
})();
setInterval(updateRealtimeCountdowns, 1000);
