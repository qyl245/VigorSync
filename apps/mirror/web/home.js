import { mountWeather } from "./modules/weather.js";
import { mountTrend } from "./modules/trend.js";
import { mountTodo } from "./modules/todo.js";
import { mountWeeklyPlan } from "./modules/weekly-plan.js";

const query = new URLSearchParams(window.location.search);
const CORE_URL = (query.get("core") || "http://127.0.0.1:8011").replace(/\/$/, "");

const elements = {
  connection: document.querySelector("#home-connection"),
  connectionText: document.querySelector("#home-connection-text"),
  motto: document.querySelector("#home-motto"),
  date: document.querySelector("#home-date"),
  time: document.querySelector("#home-time"),
  columnLeft: document.querySelector("#home-column-left"),
  columnRight: document.querySelector("#home-column-right"),
};

const MODULE_NAMES = {
  weather: "天气",
  trend: "训练趋势",
  todo: "今日待办",
  weekly_plan: "本周计划",
};

const MODULE_MOUNTS = {
  weather: mountWeather,
  trend: mountTrend,
  todo: mountTodo,
  weekly_plan: mountWeeklyPlan,
};

const DEFAULT_LAYOUT = {
  version: 2,
  modules: [
    { id: "weather", size: "1x2", column: "left", order: 0 },
    { id: "todo", size: "1x2", column: "left", order: 1 },
    { id: "trend", size: "1x2", column: "right", order: 0 },
    { id: "weekly_plan", size: "1x2", column: "right", order: 1 },
  ],
};

const MOTTOS = [
  { from: 5, to: 11, text: "新的一天，从一组开始" },
  { from: 11, to: 14, text: "让每一次发力可见" },
  { from: 14, to: 18, text: "看清姿态，再进一步" },
  { from: 18, to: 23, text: "让每一次发力可见" },
  { from: 23, to: 24, text: "休息也是训练的一部分" },
  { from: 0, to: 5, text: "休息也是训练的一部分" },
];

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

const moduleCleanups = [];

async function fetchJson(path) {
  const response = await fetch(`${CORE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`请求失败 (${response.status})`);
  }
  return response.json();
}

function tickClock() {
  const now = new Date();
  const hour = now.getHours();
  elements.time.textContent =
    `${String(hour).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  elements.date.textContent =
    `${now.getMonth() + 1}月${now.getDate()}日 ${WEEKDAYS[now.getDay()]}`;
  const motto = MOTTOS.find((item) => hour >= item.from && hour < item.to);
  elements.motto.textContent = motto ? motto.text : "让每一次发力可见";
}

function setPairing(connected, pairingCode) {
  elements.connection.classList.toggle("online", connected);
  elements.connectionText.textContent = connected
    ? "手机已连接"
    : `手机未连接 · 配对码 ${pairingCode}`;
}

async function refreshPairingStatus() {
  try {
    const status = await fetchJson("/dev/v1/pairing/status");
    setPairing(Boolean(status.connected), status.pairing_code || "482913");
  } catch {
    // Keep the last known state when Core is unreachable.
  }
}

function mountPlaceholder(module, host) {
  const card = document.createElement("article");
  card.className = `home-module home-module-${module.size} is-placeholder`;
  card.innerHTML = `
    <p class="home-module-label">${MODULE_NAMES[module.id] || module.id}</p>
    <p class="home-module-empty">即将上线</p>
  `;
  host.appendChild(card);
}

function renderLayout(layout) {
  moduleCleanups.splice(0).forEach((cleanup) => cleanup());
  elements.columnLeft.replaceChildren();
  elements.columnRight.replaceChildren();
  const modules = [...(layout?.modules || [])].sort((a, b) => a.order - b.order);
  for (const module of modules) {
    const host = module.column === "right" ? elements.columnRight : elements.columnLeft;
    const mount = MODULE_MOUNTS[module.id];
    if (!mount) {
      mountPlaceholder(module, host);
      continue;
    }
    const card = document.createElement("article");
    card.className = `home-module home-module-${module.size}`;
    host.appendChild(card);
    try {
      const cleanup = mount(card, CORE_URL);
      if (typeof cleanup === "function") {
        moduleCleanups.push(cleanup);
      }
    } catch {
      mountPlaceholder(module, host);
      card.remove();
    }
  }
}

async function refreshLayout() {
  try {
    renderLayout(await fetchJson("/dev/v1/layout"));
  } catch {
    if (!elements.columnLeft.childElementCount && !elements.columnRight.childElementCount) {
      renderLayout(DEFAULT_LAYOUT);
    }
  }
}

window.addEventListener("vigorsync:mirror.layout", (event) => {
  renderLayout(event.detail);
});

window.addEventListener("vigorsync:pairing.state", (event) => {
  const connected = Boolean(event.detail?.connected);
  const current = elements.connectionText.textContent.match(/配对码 (\d{6})/);
  setPairing(connected, current ? current[1] : "482913");
});

tickClock();
window.setInterval(tickClock, 1000);
refreshPairingStatus();
refreshLayout();
