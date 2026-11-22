export function mountWeather(card, coreUrl) {
  card.innerHTML = `
    <p class="home-module-label">天气 · <span class="weather-location">正在定位</span></p>
    <div class="weather-main">
      <div>
        <div class="weather-reading">
          <strong class="weather-temp">--</strong>
          <span class="weather-unit">°C</span>
        </div>
        <p class="weather-text">--</p>
      </div>
      <span class="weather-symbol" aria-hidden="true">·</span>
    </div>
    <p class="weather-meta">湿度 --</p>
    <p class="weather-range">今日 --</p>
  `;
  const locationEl = card.querySelector(".weather-location");
  const tempEl = card.querySelector(".weather-temp");
  const textEl = card.querySelector(".weather-text");
  const symbolEl = card.querySelector(".weather-symbol");
  const metaEl = card.querySelector(".weather-meta");
  const rangeEl = card.querySelector(".weather-range");

  function weatherSymbol(code) {
    const value = Number(code);
    if (value === 0 || value === 1) return "☀";
    if (value === 2 || value === 3) return "☁";
    if (value === 45 || value === 48) return "≋";
    if ((value >= 51 && value <= 67) || (value >= 80 && value <= 82)) return "☂";
    if ((value >= 71 && value <= 77) || value === 85 || value === 86) return "❄";
    if (value >= 95) return "ϟ";
    return "·";
  }

  function apply(data) {
    if (!data || data.status !== "ok") {
      locationEl.textContent = "位置不可用";
      tempEl.textContent = "--";
      textEl.textContent = "离线";
      symbolEl.textContent = "·";
      metaEl.textContent = "湿度 --";
      rangeEl.textContent = "今日 --";
      return;
    }
    const temp = Number(data.temperature_c);
    locationEl.textContent = data.location || "本地";
    tempEl.textContent = Number.isFinite(temp) ? String(Math.round(temp)) : "--";
    textEl.textContent = data.weather_text || "--";
    symbolEl.textContent = weatherSymbol(data.weather_code);
    const humidity = Number(data.humidity);
    const freshness = data.stale ? "缓存" : "实时";
    metaEl.textContent = Number.isFinite(humidity)
      ? `湿度 ${Math.round(humidity)}% · ${freshness}`
      : `湿度 -- · ${freshness}`;
    const min = Number(data.temp_min_c);
    const max = Number(data.temp_max_c);
    rangeEl.textContent =
      Number.isFinite(min) && Number.isFinite(max)
        ? `今日 ${Math.round(min)}° – ${Math.round(max)}°`
        : "今日 --";
  }

  async function refresh() {
    try {
      const response = await fetch(`${coreUrl}/dev/v1/weather`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      apply(await response.json());
    } catch {
      apply(null);
    }
  }

  refresh();
  const timer = window.setInterval(refresh, 10 * 60 * 1000);
  const retry = window.setTimeout(refresh, 60 * 1000);
  const onOnline = () => refresh();
  window.addEventListener("online", onOnline);
  return () => {
    window.clearInterval(timer);
    window.clearTimeout(retry);
    window.removeEventListener("online", onOnline);
  };
}
