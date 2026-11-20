export function mountTodo(card, coreUrl) {
  card.innerHTML = `
    <p class="home-module-label">今日待办 · <span class="todo-progress">0/0</span></p>
    <ul class="todo-list" aria-label="今日待办列表"></ul>
    <p class="todo-empty">今天还没有待办</p>
  `;
  const progressEl = card.querySelector(".todo-progress");
  const listEl = card.querySelector(".todo-list");
  const emptyEl = card.querySelector(".todo-empty");

  function render(data) {
    const items = Array.isArray(data?.items) ? data.items : [];
    const completed = Number(data?.completed) || 0;
    const total = Number(data?.total) || items.length;
    progressEl.textContent = `${completed}/${total}`;
    listEl.replaceChildren();
    emptyEl.hidden = items.length > 0;
    for (const item of items) {
      const row = document.createElement("li");
      row.className = "todo-item";
      if (item.completed) row.classList.add("is-complete");
      const marker = document.createElement("span");
      marker.className = "todo-marker";
      marker.setAttribute("aria-hidden", "true");
      const title = document.createElement("span");
      title.className = "todo-title";
      title.textContent = item.title || "";
      row.append(marker, title);
      listEl.appendChild(row);
    }
  }

  async function refresh() {
    try {
      const response = await fetch(`${coreUrl}/dev/v1/todos`);
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      render(await response.json());
    } catch {
      if (!listEl.childElementCount) emptyEl.hidden = false;
    }
  }

  const onUpdated = (event) => render(event.detail);
  window.addEventListener("vigorsync:todo.updated", onUpdated);
  refresh();
  const timer = window.setInterval(refresh, 5 * 60 * 1000);
  return () => {
    window.clearInterval(timer);
    window.removeEventListener("vigorsync:todo.updated", onUpdated);
  };
}
