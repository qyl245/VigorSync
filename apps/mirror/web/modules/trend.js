const DAY_LABELS = ["日", "一", "二", "三", "四", "五", "六"];

export function mountTrend(card, coreUrl) {
  card.innerHTML = `
    <p class="home-module-label">训练趋势 · 近 7 天</p>
    <div class="trend-chart" role="img" aria-label="近七天训练时长柱状图"></div>
    <div class="trend-days"></div>
    <p class="trend-summary">本周 -- 次 · -- 分钟</p>
  `;
  const chartEl = card.querySelector(".trend-chart");
  const daysEl = card.querySelector(".trend-days");
  const summaryEl = card.querySelector(".trend-summary");

  function render(days) {
    chartEl.replaceChildren();
    daysEl.replaceChildren();
    const items = Array.isArray(days) ? days.slice(-7) : [];
    const today = new Date();
    const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    const maxMinutes = Math.max(1, ...items.map((day) => Number(day.duration_s) / 60 || 0));
    let totalSessions = 0;
    let totalMinutes = 0;
    items.forEach((day) => {
      const minutes = Number(day.duration_s) / 60 || 0;
      const sessions = Number(day.sessions) || 0;
      totalSessions += sessions;
      totalMinutes += minutes;

      const bar = document.createElement("div");
      bar.className = "trend-bar";
      if (day.date === todayKey) bar.classList.add("is-today");
      const height = Math.max(4, Math.round((minutes / maxMinutes) * 100));
      bar.innerHTML = `
        <span class="trend-bar-value">${minutes >= 1 ? Math.round(minutes) : ""}</span>
        <span class="trend-bar-fill" style="height:${minutes > 0 ? height : 4}%"></span>
      `;
      chartEl.appendChild(bar);

      const label = document.createElement("span");
      label.className = "trend-day";
      if (day.date === todayKey) {
        label.classList.add("is-today");
        label.textContent = "今天";
      } else {
        const parsed = new Date(`${day.date}T00:00:00`);
        label.textContent = Number.isNaN(parsed.getTime()) ? "--" : DAY_LABELS[parsed.getDay()];
      }
      daysEl.appendChild(label);
    });
    summaryEl.textContent = items.length
      ? `本周 ${totalSessions} 次 · ${Math.round(totalMinutes)} 分钟`
      : "暂无训练记录";
  }

  async function refresh() {
    try {
      const response = await fetch(`${coreUrl}/dev/v1/trend`);
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      render((await response.json()).days);
    } catch {
      render(null);
    }
  }

  refresh();
  const timer = window.setInterval(refresh, 5 * 60 * 1000);
  return () => window.clearInterval(timer);
}
