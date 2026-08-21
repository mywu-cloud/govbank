# 八大官股行庫買賣追蹤

八大公股銀行（八大官股行庫）每日買賣動向與買賣超排行網站。

- **資料來源**：FinMind 付費 API（`TaiwanStockGovernmentBankBuySell` + `TaiwanStockPrice` + `TaiwanStockInfo`）
- **資料庫**：Cloudflare D1
- **後端**：GitHub Actions（排程抓資料）+ Cloudflare Pages Functions（API）
- **前端**：靜態 HTML/CSS/JS + Chart.js，部署在 Cloudflare Pages

## 目錄結構

```
.github/workflows/fetch-data.yml   # 排程：每個交易日收盤後抓資料寫入 D1
backend/
  fetch_data.py                    # 抓取腳本（FinMind -> D1）
  schema.sql                       # D1 資料表結構
  requirements.txt
functions/api/
  trend.js                         # GET /api/trend   （分頁1：買賣動向）
  ranking.js                       # GET /api/ranking （分頁2：買賣超排行）
frontend/
  index.html                       # 分頁1
  ranking.html                     # 分頁2
  css/style.css
  js/trend.js
  js/ranking.js
wrangler.toml
```

## 部署步驟

### 1. 建立 Cloudflare D1 資料庫

```bash
npm install -g wrangler
wrangler login
wrangler d1 create govbank-db
```

會印出一組 `database_id`，貼到 `wrangler.toml` 的 `database_id` 欄位。

建表：

```bash
wrangler d1 execute govbank-db --file=backend/schema.sql --remote
```

### 2. 建立 GitHub repo 並 push

```bash
cd govbank
git init
git add .
git commit -m "init: 八大官股行庫追蹤網站"
git remote add origin <你的 GitHub repo URL>
git push -u origin main
```

在 GitHub repo → Settings → Secrets and variables → Actions，新增：

| Secret 名稱 | 說明 |
|---|---|
| `FINMIND_TOKEN` | FinMind 付費 API token |
| `CF_ACCOUNT_ID` | Cloudflare 帳號 ID（Dashboard 右側欄可查） |
| `CF_DATABASE_ID` | 上一步建立的 D1 database_id |
| `CF_API_TOKEN` | Cloudflare API Token，需有 **D1 Edit** 權限 |

之後每個交易日台北時間 15:00 會自動抓資料；也可以到 Actions 分頁手動 `Run workflow` 先測一次。

### 3. 串接 Cloudflare Pages

Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git，選這個 repo：

- Build output directory：`frontend`
- Framework preset：None（純靜態）
- 部署完成後，到該 Pages 專案 → Settings → Functions → D1 database bindings：
  - Variable name：`DB`
  - D1 database：選 `govbank-db`

這樣 `functions/api/*.js` 才能透過 `env.DB` 存取資料庫。

### 4. 驗證

1. 先手動觸發一次 GitHub Actions，確認 D1 裡有資料（`wrangler d1 execute govbank-db --command="SELECT * FROM gov_bank_stock_daily LIMIT 5" --remote`）
2. 打開 Cloudflare Pages 給的網址，確認兩個分頁都能讀到資料

## 待確認事項（上線前務必實測）

`backend/fetch_data.py` 檔首有註解說明：`TaiwanStockGovernmentBankBuySell` 資料集的實際欄位名稱（張數/金額怎麼給、市場別欄位格式）是依 FinMind 文件慣例推測撰寫，**必須用你的付費 token 實際呼叫一次確認欄位**，再對照腳本裡標示 `FIELD MAP` 的區塊做微調。由於 Claude 沙箱無法連線 api.finmindtrade.com，這步無法在對話中先幫你驗證。

## 之後可以擴充的方向

- 排行榜目前是「區間加總排序＋帶最後一日收盤」，如果想看「每日明細」而非加總，`ranking.js` 的 SQL 可以改成不 GROUP BY，直接照日期展開
- 可以加一個「個股歷史八大官股買賣走勢」頁面（類似 histock 個股頁的走勢圖），做法是複用 `trend.js` 的圖表邏輯，把 API 改成用 `stock_id` 查 `gov_bank_stock_daily`
# govbank
