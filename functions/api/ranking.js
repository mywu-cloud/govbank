// GET /api/ranking
// 分頁2：八大官股行庫「買賣超排行」（區間加總排序，帶最後一日收盤）
//
// Query params:
//   start - YYYY-MM-DD，預設抓「最近 30 個有資料的交易日」的第一天
//   end   - YYYY-MM-DD，預設資料庫裡最新的交易日
//   order - net_amount(預設) | net_buy | buy_amount | sell_amount
//   dir   - desc(預設，買超排行) | asc（賣超排行）
//   limit - 預設 50
//
// Response:
// {
//   start, end, min_date, max_date,
//   rows: [{ stock_id, stock_name, industry_category, close,
//            buy, sell, buy_amount, sell_amount, net_buy, net_amount, days }]
// }

const ORDER_COLUMNS = {
  net_amount: "net_amount",
  net_buy: "net_buy",
  buy_amount: "buy_amount",
  sell_amount: "sell_amount",
};

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "public, max-age=60",
      "access-control-allow-origin": "*",
    },
  });
}

export async function onRequestGet(context) {
  const { env, request } = context;
  const db = env.DB;
  const url = new URL(request.url);

  try {
    const range = await db
      .prepare("SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM gov_bank_daily")
      .first();

    if (!range || !range.max_date) {
      return jsonResponse({ start: null, end: null, min_date: null, max_date: null, rows: [] });
    }

    const dates = await db
      .prepare("SELECT DISTINCT date FROM gov_bank_daily ORDER BY date DESC LIMIT 30")
      .all();
    const recentDates = dates.results.map((r) => r.date);
    const defaultStart = recentDates[recentDates.length - 1];

    const qsStart = url.searchParams.get("start");
    const qsEnd = url.searchParams.get("end");
    const start = qsStart && /^\d{4}-\d{2}-\d{2}$/.test(qsStart) ? qsStart : defaultStart;
    const end = qsEnd && /^\d{4}-\d{2}-\d{2}$/.test(qsEnd) ? qsEnd : range.max_date;

    const orderKey = ORDER_COLUMNS[url.searchParams.get("order")] || "net_amount";
    const dir = url.searchParams.get("dir") === "asc" ? "ASC" : "DESC";
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10) || 50, 500);

    const sql = `
      SELECT
        g.stock_id AS stock_id,
        COALESCE(si.stock_name, '') AS stock_name,
        COALESCE(si.industry_category, '') AS industry_category,
        SUM(g.buy) AS buy,
        SUM(g.sell) AS sell,
        SUM(g.buy_amount) AS buy_amount,
        SUM(g.sell_amount) AS sell_amount,
        (SUM(g.buy) - SUM(g.sell)) AS net_buy,
        (SUM(g.buy_amount) - SUM(g.sell_amount)) AS net_amount,
        COUNT(DISTINCT g.date) AS days,
        (
          SELECT sp.close FROM stock_price_daily sp
          WHERE sp.stock_id = g.stock_id AND sp.date <= ?
          ORDER BY sp.date DESC LIMIT 1
        ) AS close
      FROM gov_bank_daily g
      LEFT JOIN stock_info si ON si.stock_id = g.stock_id
      WHERE g.date BETWEEN ? AND ?
      GROUP BY g.stock_id
      ORDER BY ${orderKey} ${dir}
      LIMIT ?
    `;
    const agg = await db.prepare(sql).bind(end, start, end, limit).all();

    return jsonResponse({
      start,
      end,
      min_date: range.min_date,
      max_date: range.max_date,
      rows: agg.results,
    });
  } catch (err) {
    return jsonResponse({ error: String(err && err.message ? err.message : err) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET, OPTIONS",
    },
  });
}
