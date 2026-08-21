-- 八大官股行庫買賣追蹤 - D1 資料表結構
-- 執行方式：wrangler d1 execute govbank-db --file=backend/schema.sql --remote

-- 八大官股行庫每日買賣明細（依 FinMind TaiwanStockGovernmentBankBuySell 資料集，
-- 每一列 = 某交易日、某股票、某一家官股銀行的買賣資料）
CREATE TABLE IF NOT EXISTS gov_bank_daily (
  date         TEXT    NOT NULL,           -- 交易日 YYYY-MM-DD
  stock_id     TEXT    NOT NULL,           -- 股票代號
  bank_name    TEXT    NOT NULL,           -- 官股銀行名稱（例如：合作金庫、第一銀行、華南銀行、彰化銀行、兆豐銀行、台灣企銀、臺灣銀行、土地銀行）
  buy          INTEGER NOT NULL DEFAULT 0, -- 買進股數
  sell         INTEGER NOT NULL DEFAULT 0, -- 賣出股數
  buy_amount   INTEGER NOT NULL DEFAULT 0, -- 買進金額（元）
  sell_amount  INTEGER NOT NULL DEFAULT 0, -- 賣出金額（元）
  updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (date, stock_id, bank_name)
);

CREATE INDEX IF NOT EXISTS idx_gov_bank_daily_date  ON gov_bank_daily(date);
CREATE INDEX IF NOT EXISTS idx_gov_bank_daily_stock ON gov_bank_daily(stock_id);

-- 股票基本資料（FinMind TaiwanStockInfo，用來把 stock_id 轉成中文股票名稱 / 產業別）
CREATE TABLE IF NOT EXISTS stock_info (
  stock_id           TEXT PRIMARY KEY,
  stock_name         TEXT,
  industry_category  TEXT,
  updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 個股每日收盤價（FinMind TaiwanStockPrice，排行榜要帶「最後一日收盤」）
CREATE TABLE IF NOT EXISTS stock_price_daily (
  date             TEXT    NOT NULL,
  stock_id         TEXT    NOT NULL,
  close            REAL,
  spread           REAL,
  trading_volume   INTEGER,
  updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (date, stock_id)
);

CREATE INDEX IF NOT EXISTS idx_stock_price_daily_date ON stock_price_daily(date);

-- 記錄每次排程抓取的執行狀態，方便除錯（哪一天資料抓到、抓了幾筆）
CREATE TABLE IF NOT EXISTS fetch_log (
  date         TEXT PRIMARY KEY,
  row_count    INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'ok', -- ok / empty / error
  message      TEXT,
  fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
