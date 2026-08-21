// GET /api/trend
// 分頁1：八大官股行庫「單日買賣動向」
//
// Query params:
//   date  - YYYY-MM-DD，預設騐抓資料庫裡最新的一個交易日
//   order - net_amount(預設) | net_buy | buy_amount | sell_amount
//   dir   - desc(預設) | asc
//   limit - 預設 500
//
// Response:
// {
//   date, dates: [最近可查詢的交易日, 由新到舊],
//   rows: [{ stock_id, stock_name, industry_category, close, spread,
//            buy, sell, buy_amount, sell_amount, net_buy, net_amount,
//            banks: [{ bank_name, buy, sell, buy_amount, sell_amount }] }]
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
    const dates = await db
      .prepare("SELECT DISTINCT date FROM gov_bank_daily ORDER BY date DESC LIMIT 30")
      .all();
    const dateList = dates.results.map((r) => r.date);

    if (dateList.length === 0) {
      return jsonResponse({ date: null, dates: [], rows: [] });
    }

    const requestedDate = url.searchParams.get("date");
    const targetDate =
      requestedDate && /^\d{4}-\d{2}-\d{2}$/.test(requestedDate) ? requestedDate : dateList[0];

    const orderKey = ORDER_COLUMNS[url.searchParams.get("order")] || "net_amount";
    const dir = url.searchParams.get("dir") === "asc" ? "ASC" : "DESC";
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "500", 10) || 500, 2000);

    const aggSql = `
      SELECT
        g.stock_id AS stock_id,
        COALESCE(si.stock_name, '') AS stock_name,
        COALESCE(si.industry_category, '') AS industry_category,
        sp.close AS close,
        sp.spread AS spread,
        SUM(g.buy) AS buy,
        SUM(g.sell) AS sell,
        SUM(g.buy_amount) AS buy_amount,
        SUM(g.sell_amount) AS sell_amount,
        (SUM(g.buy) - SUM(g.sell)) AS net_buy,
        (SUM(g.buy_amount) - SUM(g.sell_amount)) AS net_amount
      FROM gov_bank_daily g
      LEFT JOIN stock_info si ON si.stock_id = g.stock_id
      LEFT JOIN stock_price_daily sp ON sp.stock_id = g.stock_id AND sp.date = g.date
      WHERE g.date = ?
      GROUP BY g.stock_id
      ORDER BY ${orderKey} ${dir}
      LIMIT ?
    `;
    const agg = await db.prepare(aggSql).bind(targetDate, limit).all();

    const bankSql = `
      SELECT stock_id, bank_name, buy, sell, buy_amount, sell_amount
      FROM gov_bank_daily
      WHERE date = ?
    `;
    const bankRows = await db.prepare(bankSql).bind(targetDate).all();

    const banksByStock = new Map();
    for (const r of bankRows.results) {
      if (!banksByStock.has(r.stock_id)) banksByStock.set(r.stock_id, []);
      banksByStock.get(r.stock_id).push({
        bank_name: r.bank_name,
        buy: r.buy,
        sell: r.sell,
        buy_amount: r.buy_amount,
        sell_amount: r.sell_amount,
      });
    }

    const rows = agg.results.map((r) => ({
      ...r,
      banks: (banksByStock.get(r.stock_id) || []).sort(
        (a, b) => b.buy_amount - b.sell_amount - (a.buy_amount - a.sell_amount)
      ),
    }));

    return jsonResponse({ date: targetDate, dates: dateList, rows });
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
