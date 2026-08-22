(() => {
  const dateSelect = document.getElementById("date-select");
  const statusText = document.getElementById("status-text");
  const tbody = document.getElementById("trend-tbody");
  const emptyState = document.getElementById("empty-state");
  const chartTitle = document.getElementById("chart-title");
  const orderGroup = document.getElementById("order-group");
  const table = document.getElementById("trend-table");

  let currentRows = [];
  let currentOrder = "net_amount";
  let sortState = { key: "net_amount", dir: "desc" };
  let chart = null;

  const fmtLots = (shares) => Math.round((shares || 0) / 1000).toLocaleString("zh-Hant");
  const fmtWan = (amount) =>
    Math.round((amount || 0) / 10000).toLocaleString("zh-Hant");
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

  async function loadDates() {
    const res = await fetch("/api/trend");
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (!data.dates || data.dates.length === 0) {
      statusText.textContent = "尚無資料";
      emptyState.style.display = "block";
      table.style.display = "none";
      return null;
    }
    dateSelect.innerHTML = data.dates
      .map((d) => `<option value="${d}">${d}</option>`)
      .join("");
    dateSelect.value = data.date;
    return data;
  }

  async function loadTrend(date) {
    statusText.textContent = "載入中…";
    const url = date ? `/api/trend?date=${encodeURIComponent(date)}` : "/api/trend";
    const res = await fetch(url);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    currentRows = data.rows || [];
    sortState = { key: currentOrder, dir: "desc" };
    renderAll();
    statusText.textContent = `${data.date || "—"}・共 ${currentRows.length} 檔`;
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
    const top = [...rows]
      .filter((r) => r[key] !== 0)
      .sort((a, b) => Math.abs(b[key]) - Math.abs(a[key]))
      .slice(0, 15);

    const labels = top.map((r) => `${r.stock_name || r.stock_id}`);
    const values = top.map((r) => (key === "net_amount" ? Math.round(r[key] / 10000) : Math.round(r[key] / 1000)));
    const upColor = cssVar("--up", "#d92d20");
    const downColor = cssVar("--down", "#16874f");
    const colors = values.map((v) => (v >= 0 ? upColor : downColor));

    chartTitle.textContent =
      key === "net_amount" ? "當日買超 / 賣超金額 Top 15（萬元）" : "當日買超 / 賣超張數 Top 15（張）";

    const ctx = document.getElementById("trend-chart");
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
      .map((r, i) => {
        const rowId = `bank-${i}`;
        const banks = (r.banks || [])
          .map((b) => {
            const net = (b.buy_amount || 0) - (b.sell_amount || 0);
            return `<span class="bank-chip"><span class="bank-name">${b.bank_name}</span><span class="${numClass(net)}">${fmtWan(net)}萬</span></span>`;
          })
          .join("");
        return `
          <tr class="data-row" data-target="${rowId}">
            <td class="stock-id">${r.stock_id}</td>
            <td class="stock-name">${r.stock_name || "—"} <button class="expand-btn">展開</button></td>
            <td><span class="pill">${r.industry_category || "—"}</span></td>
            <td>${fmtPrice(r.close)}</td>
            <td>${fmtLots(r.buy)}</td>
            <td>${fmtLots(r.sell)}</td>
            <td class="${numClass(r.net_buy)}">${fmtLots(r.net_buy)}</td>
            <td class="${numClass(r.net_amount)}">${fmtWan(r.net_amount)}</td>
          </tr>
          <tr class="bank-row" id="${rowId}">
            <td colspan="8" class="bank-detail"><div class="bank-chips">${banks || "（無明細）"}</div></td>
          </tr>
        `;
      })
      .join("");

    tbody.querySelectorAll(".data-row").forEach((tr) => {
      tr.addEventListener("click", () => {
        const target = document.getElementById(tr.dataset.target);
        target.classList.toggle("open");
      });
    });
  }

  function renderAll() {
    const rows = sortedRows();
    renderChart(rows);
    renderTable(rows);
    table.querySelectorAll("th").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === sortState.key);
    });
  }

  dateSelect.addEventListener("change", () => loadTrend(dateSelect.value));

  orderGroup.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-order]");
    if (!btn) return;
    orderGroup.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentOrder = btn.dataset.order;
    sortState = { key: currentOrder, dir: "desc" };
    renderAll();
  });

  table.querySelector("thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-key]");
    if (!th) return;
    const key = th.dataset.key;
    if (sortState.key === key) {
      sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
    } else {
      sortState = { key, dir: "desc" };
    }
    renderAll();
  });

  // 切換淺色/深色主題時，圖表要用新的顏色重畫。
  window.addEventListener("govbank:themechange", () => {
    if (currentRows.length) renderChart(sortedRows());
  });

  (async function init() {
    try {
      const first = await loadDates();
      if (first) {
        currentRows = first.rows || [];
        sortState = { key: currentOrder, dir: "desc" };
        renderAll();
        statusText.textContent = `${first.date || "—"}・共 ${currentRows.length} 檔`;
      }
    } catch (err) {
      statusText.textContent = "載入失敗：" + err.message;
      console.error(err);
    }
  })();
})();
