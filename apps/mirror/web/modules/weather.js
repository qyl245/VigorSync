export function mountWeather(card, coreUrl) {
  card.innerHTML = `
    <p class="home-module-label">天气</p>
    <div class="weather-reading">
      <strong class="weather-temp">--</strong>
      <span class="weather-unit">°C</span>
    </div>
    <p class="weather-text">--</p>
    <p class="weather-meta">湿度 --</p>
    <p class="weather-range">今日 --</p>
  `;
  const tempEl = card.querySelector(".weather-temp");
  const textEl = card.querySelector(".weather-text");
  const metaEl = card.querySelector(".weather-meta");
  const rangeEl = card.querySelector(".weather-range");

  function apply(data) {
    if (!data || data.status !== "ok") {
      tempEl.textContent = "--";
      textEl.textContent = "离线";
      metaEl.textContent = "湿度 --";
      rangeEl.textContent = "今日 --";
      return;
    }
    const temp = Number(data.temperature_c);
    tempEl.textContent = Number.isFinite(temp) ? String(Math.round(temp)) : "--";
    textEl.textContent = data.weather_text || "--";
    const humidity = Number(data.humidity);
    metaEl.textContent = Number.isFinite(humidity) ? `湿度 ${Math.round(humidity)}%` : "湿度 --";
    const min = Number(data.temp_min_c);
    const max = Number(data.temp_max_c);
    rangeEl.textContent =
      Number.isFinite(min) && Number.isFinite(max)
        ? `今日 ${Math.round(min)}° – ${Math.round(max)}°`
        : "今日 --";
  }

  async function refresh() {
    try {
      const response = await fetch(`${coreUrl}/dev/v1/weather`);
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      apply(await response.json());
    } catch {
      apply(null);
    }
  }

  refresh();
  const timer = window.setInterval(refresh, 30 * 60 * 1000);
  return () => window.clearInterval(timer);
}
