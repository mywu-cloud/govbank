(() => {
  const startInput = document.getElementById("start-date");
  const endInput = document.getElementById("end-date");
  const statusText = document.getElementById("status-text");
  const tbody = document.getElementById("ranking-tbody");
  const emptyState = document.getElementById("empty-state");
  const chartTitle = document.getElementById("chart-title");
  const directionGroup = document.getElementById("direction-group");
  const orderGroup = document.getElementById("order-group");
  const table = document.getElementById("ranking-table");

  let currentRows = [];
  let currentOrder = "net_amount";
  let currentDir = "desc";
  let sortState = { key: "net_amount", dir: "desc" };
  let chart = null;

  const fmtLots = (shares) => Math.round((shares || 0) / 1000).toLocaleString("zh-Hant");
  const fmtWan = (amount) => Math.round((amount || 0) / 10000).toLocaleString("zh-Hant");
  const fmtPrice = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));

  function numClass(v) {
    if (v > 0) return "num-up";
    if (v < 0) return "num-down";
    return "num-flat";
  }

  // Chart.js 的顏色是用 JS 直接設定，不會自動跟著 CSS 變數（主題）變化，
  // 所以每次畫圖前都從目前套用的 CSS 變數讀取顏色。
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function buildUrl() {
    const params = new URLSearchParams();
    if (startInput.value) params.set("start", startInput.value);
    if (endInput.value) params.set("end", endInput.value);
    params.set("order", currentOrder);
    params.set("dir", currentDir);
    params.set("limit", "50");
    return `/api/ranking?${params.toString()}`;
  }

  async function load(initial = false) {
    statusText.textContent = "載入中…";
    const res = await fetch(initial ? "/api/ranking" : buildUrl());
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    if (!data.max_date) {
      statusText.textContent = "尚無資料";
      emptyState.style.display = "block";
      table.style.display = "none";
      return;
    }

    if (initial) {
      startInput.value = data.start;
      endInput.value = data.end;
      startInput.min = data.min_date;
      startInput.max = data.max_date;
      endInput.min = data.min_date;
      endInput.max = data.max_date;
    }

    currentRows = data.rows || [];
    sortState = { key: currentOrder, dir: currentDir };
    renderAll();
    statusText.textContent = `${data.start} ~ ${data.end}・共 ${currentRows.length} 檔`;
  }

  function sortedRows() {
    const { key, dir } = sortState;
    const rows = [...currentRows];
    rows.sort((a, b) => {
      let va = a[key];
      let vb = b[key];
      if (typeof va === "string" || typeof vb === "string") {
        va = va || "";
        vb = vb || "";
        return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      va = va || 0;
      vb = vb || 0;
      return dir === "asc" ? va - vb : vb - va;
    });
    return rows;
  }

  function renderChart(rows) {
    const key = currentOrder;
    const top = rows.slice(0, 20);
    const labels = top.map((r) => `${r.stock_name || r.stock_id}`);
    const values = top.map((r) =>
      key === "net_amount" ? Math.round(r[key] / 10000) : Math.round(r[key] / 1000)
    );
    const upColor = cssVar("--up", "#d92d20");
    const downColor = cssVar("--down", "#16874f");
    const colors = values.map((v) => (v >= 0 ? upColor : downColor));

    const label = currentDir === "asc" ? "賣超排行" : "買超排行";
    chartTitle.textContent =
      key === "net_amount"
        ? `區間${label} Top 20（萬元）`
        : `區間${label} Top 20（張）`;

    const ctx = document.getElementById("ranking-chart");
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: key === "net_amount" ? "買超金額(萬)" : "買超張數(張)",
            data: values,
            backgroundColor: colors,
            borderRadius: 4,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            grid: { color: cssVar("--border", "#dde3ee") },
            ticks: { color: cssVar("--text-dim", "#5b6478"), font: { size: 12 } },
          },
          y: {
            grid: { display: false },
            ticks: { color: cssVar("--text", "#1a2233"), font: { size: 12 } },
          },
        },
      },
    });
  }

  function renderTable(rows) {
    if (rows.length === 0) {
      emptyState.style.display = "block";
      table.style.display = "none";
      return;
    }
    emptyState.style.display = "none";
    table.style.display = "";

    tbody.innerHTML = rows
      .map(
        (r, i) => `
          <tr>
            <td>${i + 1}</td>
            <td class="stock-id">${r.stock_id}</td>
            <td class="stock-name">${r.stock_name || "—"}</td>
            <td><span class="pill">${r.industry_category || "—"}</span></td>
            <td>${fmtPrice(r.close)}</td>
            <td>${r.days}</td>
            <td>${fmtLots(r.buy)}</td>
            <td>${fmtLots(r.sell)}</td>
            <td class="${numClass(r.net_buy)}">${fmtLots(r.net_buy)}</td>
            <td class="${numClass(r.net_amount)}">${fmtWan(r.net_amount)}</td>
          </tr>
        `
      )
      .join("");
  }

  function renderAll() {
    const rows = sortedRows();
    renderChart(rows);
    renderTable(rows);
    table.querySelectorAll("th").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === sortState.key);
    });
  }

  [startInput, endInput].forEach((el) =>
    el.addEventListener("change", () => load(false).catch(showError))
  );

  directionGroup.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-dir]");
    if (!btn) return;
    directionGroup.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentDir = btn.dataset.dir;
    load(false).catch(showError);
  });

  orderGroup.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-order]");
    if (!btn) return;
    orderGroup.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentOrder = btn.dataset.order;
    load(false).catch(showError);
  });

  table.querySelector("thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-key]");
    if (!th || th.dataset.key === "rank") return;
    const key = th.dataset.key;
    if (sortState.key === key) {
      sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
    } else {
      sortState = { key, dir: "desc" };
    }
    renderAll();
  });

  function showError(err) {
    statusText.textContent = "載入失敗：" + err.message;
    console.error(err);
  }

  // 切換淺色/深色主題時，圖表要用新的顏色重畫。
  window.addEventListener("govbank:themechange", () => {
    if (currentRows.length) renderChart(sortedRows());
  });

  load(true).catch(showError);
})();
