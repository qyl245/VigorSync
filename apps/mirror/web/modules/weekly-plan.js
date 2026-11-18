const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];
const EXERCISE_LABELS = { squat: "深蹲", pushup: "俯卧撑" };

export function mountWeeklyPlan(card, coreUrl) {
  card.innerHTML = `
    <p class="home-module-label">本周训练计划</p>
    <div class="weekly-plan-list"></div>
  `;
  const listEl = card.querySelector(".weekly-plan-list");

  function itemLabel(item) {
    const exercise = EXERCISE_LABELS[item.exercise] || item.exercise || "训练";
    if (item.sets && item.target_reps) {
      return `${exercise} · ${item.sets} 组 × ${item.target_reps} 次`;
    }
    if (item.target_reps) return `${exercise} · ${item.target_reps} 次`;
    return exercise;
  }

  function render(data) {
    const items = Array.isArray(data?.items) ? data.items : [];
    const todayWeekday = new Date().getDay() || 7;
    listEl.replaceChildren();
    for (let weekday = 1; weekday <= 7; weekday += 1) {
      const row = document.createElement("section");
      row.className = "weekly-day";
      if (weekday === todayWeekday) row.classList.add("is-today");
      const dayItems = items
        .filter((item) => Number(item.weekday) === weekday)
        .sort((a, b) => Number(a.sort_order) - Number(b.sort_order));
      const heading = document.createElement("span");
      heading.className = "weekly-day-label";
      heading.textContent = weekday === todayWeekday
        ? `周${WEEKDAY_LABELS[weekday - 1]} · 今天`
        : `周${WEEKDAY_LABELS[weekday - 1]}`;
      const content = document.createElement("span");
      content.className = "weekly-day-content";
      content.textContent = dayItems.length
        ? dayItems.map(itemLabel).join(" / ")
        : "休息";
      row.append(heading, content);
      listEl.appendChild(row);
    }
  }

  async function refresh() {
    try {
      const response = await fetch(`${coreUrl}/dev/v1/weekly-plans`);
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      render(await response.json());
    } catch {
      if (!listEl.childElementCount) {
        listEl.textContent = "计划暂不可用";
        listEl.className = "weekly-plan-list is-empty";
      }
    }
  }

  const onUpdated = (event) => render(event.detail);
  window.addEventListener("vigorsync:weekly_plan.updated", onUpdated);
  refresh();
  const timer = window.setInterval(refresh, 10 * 60 * 1000);
  return () => {
    window.clearInterval(timer);
    window.removeEventListener("vigorsync:weekly_plan.updated", onUpdated);
  };
}
