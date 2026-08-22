#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八大官股行庫買賣追蹤 - 資料抓取腳本 (FinMind -> Cloudflare D1)

用途：
  每個交易日收盤後執行一次（由 .github/workflows/fetch-data.yml 排程觸發），
  向 FinMind 付費 API 抓取「當日」的：
    1. TaiwanStockGovernmentBankBuySell（八大官股行庫買賣明細，付費 sponsor 資料集）
    2. TaiwanStockPrice（個股收盤價，市場全部股票）
    3. TaiwanStockInfo（股票基本資料，只抓一次 / 每次跑都更新一下即可，量小）
  再透過 Cloudflare D1 HTTP API，把資料 upsert 進 D1 資料庫。

執行方式：
  python backend/fetch_data.py                  # 抓「今天」(Asia/Taipei) 的資料
  python backend/fetch_data.py --date 2026-08-20 # 手動補特定日期的資料

需要的環境變數（GitHub Actions secrets）：
  FINMIND_TOKEN   FinMind 付費 API token
  CF_ACCOUNT_ID   Cloudflare 帳號 ID
  CF_DATABASE_ID  D1 資料庫 ID（govbank-db）
  CF_API_TOKEN    Cloudflare API Token，需有 D1 Edit 權限

============================================================
【FIELD MAP - 待確認】
README 已經提醒過：TaiwanStockGovernmentBankBuySell 是付費 sponsor 資料集，
Claude 沙箱環境無法連線 api.finmindtrade.com，所以下面的欄位名稱、資料粒度
（是「合計」還是「逐銀行」逤是依據 FinMind 官方文件 (finmind.github.io) 目前
公開的欄位說明寫的，本腳本部署前務必用你自己的付費 token 實際打一次 API
確認，再對照下方標記 [FIELD MAP] 的區塊做微調：

  dataset: TaiwanStockGovernmentBankBuySell
  欄位（依官方文件）：
    date         str    交易日
    stock_id     str    股票代號
    bank_name    str    官股銀行名稱（逐銀行一列，不是八家合計）
    buy          int64  買進股數
    sell         int64  賣出股數
    buy_amount   int64  買進金額（元）
    sell_amount  int64  賣出金額（元）

  查詢方式：本腳本假設「不帶 data_id，只帶 start_date=end_date=某一天」
  可以拿到「當天全市場」的資料（跟 TaiwanStockInstitutionalInvestorsBuySell
  等籌碼面資料集的慣例一致）。如果實測發現「不帶 data_id 會查不到資料 /
  只能查特定股票」，就要改成先取得候選股票清單，再逐檔用 data_id 查詢。
============================================================
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TAIPEI_TZ = timezone(timedelta(hours=8))

# D1 HTTP API 單次 query 建議不要塞太多 row，避免 SQL 字串 / request body 太大
D1_BATCH_SIZE = 200

# ---------------------------------------------------------------------------
# FinMind
# ---------------------------------------------------------------------------


def finmind_get(dataset: str, token: str, start_date: str, end_date: str,
                 data_id: str | None = None, retries: int = 3) -> list[dict[str, Any]]:
    """呼叫 FinMind API，回傳 data 陣列。失敗時重試幾次。"""
    params: dict[str, Any] = {
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "token": token,
    }
    if data_id:
        params["data_id"] = data_id

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(FINMIND_URL, params=params, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != 200 and "data" not in payload:
                raise RuntimeError(f"FinMind 回傳非預期內容：{payload}")
            return payload.get("data", [])
        except Exception as err:  # noqa: BLE001
            last_err = err
            print(f"[warn] {dataset} 第 {attempt} 次呼叫失敗：{err}", file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"{dataset} 呼叫 FinMind 失敗（已重試 {retries} 次）：{last_err}")


def fetch_gov_bank_buysell(token: str, date_str: str) -> list[dict[str, Any]]:
    """[FIELD MAP] 抓當日全市場的八大官股行庫買賣明細（逐銀行、逐股票一列）。"""
    rows = finmind_get("TaiwanStockGovernmentBankBuySell", token, date_str, date_str)
    cleaned = []
    for r in rows:
        cleaned.append(
            {
                "date": r.get("date", date_str),
                "stock_id": str(r.get("stock_id", "")),
                "bank_name": r.get("bank_name", ""),
                "buy": int(r.get("buy", 0) or 0),
                "sell": int(r.get("sell", 0) or 0),
                "buy_amount": int(r.get("buy_amount", 0) or 0),
                "sell_amount": int(r.get("sell_amount", 0) or 0),
            }
        )
    return cleaned


def fetch_stock_price(token: str, date_str: str) -> list[dict[str, Any]]:
    """抓當日全市場收盤價（不帶 data_id）。"""
    rows = finmind_get("TaiwanStockPrice", token, date_str, date_str)
    cleaned = []
    for r in rows:
        cleaned.append(
            {
                "date": r.get("date", date_str),
                "stock_id": str(r.get("stock_id", "")),
                "close": r.get("close"),
                "spread": r.get("spread"),
                "trading_volume": r.get("Trading_Volume"),
            }
        )
    return cleaned


def fetch_stock_info(token: str) -> list[dict[str, Any]]:
    """抓股票基本資料（免費資料集，量小，每次都整批更新一下即可）。"""
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    rows = finmind_get("TaiwanStockInfo", token, "2000-01-01", today)
    dedup: dict[str, dict[str, Any]] = {}
    for r in rows:
        sid = str(r.get("stock_id", ""))
        if not sid:
            continue
        # 同一檔股票可能有多筆歷史資料，保留最後一筆（清單本身就是依日期排序附加的）
        dedup[sid] = {
            "stock_id": sid,
            "stock_name": r.get("stock_name", ""),
            "industry_category": r.get("industry_category", ""),
        }
    return list(dedup.values())


# ---------------------------------------------------------------------------
# Cloudflare D1 (HTTP API)
# ---------------------------------------------------------------------------


class D1Client:
    def __init__(self, account_id: str, database_id: str, api_token: str):
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    def query(self, sql: str, params: list[Any] | None = None) -> Any:
        resp = requests.post(
            self.base_url,
            headers=self.headers,
            json={"sql": sql, "params": params or []},
            timeout=60,
        )
        if not resp.ok:
            # D1 的錯誤訊息（例如「too many SQL variables」）都在 response body 裡，
            # 先印出來方便除錯，raise_for_status() 本身不會帶 body。
            print(f"[error] D1 回應 {resp.status_code}：{resp.text[:2000]}", file=sys.stderr)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success", False):
            raise RuntimeError(f"D1 query 失敗：{payload}")
        return payload.get("result", [])


def d1_upsert(db: D1Client, table: str, columns: list[str], rows: list[dict[str, Any]],
              conflict_cols: list[str]) -> int:
    """把 rows 分批 upsert 進 D1，回傳成功寫入的筆數。"""
    if not rows:
        return 0

    update_cols = [c for c in columns if c not in conflict_cols]
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)

    # D1 HTTP API 每個 query 最多只能綁 100 個參數，所以批次大小要依欄位數動態算，
    # 不能固定用 D1_BATCH_SIZE（欄位多的表格，200 筆 x 7 欄 = 1400 個參數會直接被拒絕）。
    batch_size = max(1, min(D1_BATCH_SIZE, 100 // len(columns)))

    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        placeholders_one = "(" + ", ".join(["?"] * len(columns)) + ")"
        values_sql = ", ".join([placeholders_one] * len(chunk))
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values_sql} "
            f"ON CONFLICT({', '.join(conflict_cols)}) DO UPDATE SET {set_clause};"
        )
        params: list[Any] = []
        for row in chunk:
            for c in columns:
                params.append(row.get(c))
        db.query(sql, params)
        total += len(chunk)
    return total


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取八大官股行庫買賣資料寫入 D1")
    parser.add_argument(
        "--date",
        help="要抓取的交易日 (YYYY-MM-DD)，預設抓台北時區的今天",
        default=None,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_date = args.date or os.environ.get("FETCH_DATE") or datetime.now(TAIPEI_TZ).strftime(
        "%Y-%m-%d"
    )

    finmind_token = os.environ.get("FINMIND_TOKEN")
    account_id = os.environ.get("CF_ACCOUNT_ID")
    database_id = os.environ.get("CF_DATABASE_ID")
    cf_api_token = os.environ.get("CF_API_TOKEN")

    missing = [
        name
        for name, val in (
            ("FINMIND_TOKEN", finmind_token),
            ("CF_ACCOUNT_ID", account_id),
            ("CF_DATABASE_ID", database_id),
            ("CF_API_TOKEN", cf_api_token),
        )
        if not val
    ]
    if missing:
        print(f"[error] 缺少環境變數：{', '.join(missing)}", file=sys.stderr)
        return 1

    db = D1Client(account_id, database_id, cf_api_token)

    print(f"[info] 開始抓取 {target_date} 的八大官股行庫買賣資料")

    try:
        gov_bank_rows = fetch_gov_bank_buysell(finmind_token, target_date)
    except Exception as err:  # noqa: BLE001
        print(f"[error] 抓取 TaiwanStockGovernmentBankBuySell 失敗：{err}", file=sys.stderr)
        db.query(
            "INSERT INTO fetch_log (date, row_count, status, message) VALUES (?, 0, 'error', ?) "
            "ON CONFLICT(date) DO UPDATE SET row_count=0, status='error', message=excluded.message, "
            "fetched_at=datetime('now');",
            [target_date, str(err)],
        )
        return 1

    if not gov_bank_rows:
        print(f"[warn] {target_date} 沒有抓到任何八大官股行庫買賣資料（可能非交易日 / 資料尚未更新）")
        db.query(
            "INSERT INTO fetch_log (date, row_count, status, message) VALUES (?, 0, 'empty', '無資料') "
            "ON CONFLICT(date) DO UPDATE SET row_count=0, status='empty', message='無資料', "
            "fetched_at=datetime('now');",
            [target_date],
        )
        return 0

    stock_ids = sorted({r["stock_id"] for r in gov_bank_rows})
    print(f"[info] 取得 {len(gov_bank_rows)} 筆買賣明細，涉及 {len(stock_ids)} 檔股票")

    written = d1_upsert(
        db,
        "gov_bank_daily",
        ["date", "stock_id", "bank_name", "buy", "sell", "buy_amount", "sell_amount"],
        gov_bank_rows,
        ["date", "stock_id", "bank_name"],
    )
    print(f"[info] gov_bank_daily 寫入 {written} 筆")

    # 收盤價（只保留當天有官股買賣紀錄的股票，減少寫入量）
    try:
        price_rows_all = fetch_stock_price(finmind_token, target_date)
        price_rows = [r for r in price_rows_all if r["stock_id"] in set(stock_ids)]
        written_price = d1_upsert(
            db,
            "stock_price_daily",
            ["date", "stock_id", "close", "spread", "trading_volume"],
            price_rows,
            ["date", "stock_id"],
        )
        print(f"[info] stock_price_daily 寫入 {written_price} 筆")
    except Exception as err:  # noqa: BLE001
        print(f"[warn] 抓取 TaiwanStockPrice 失敗，略過收盤價：{err}", file=sys.stderr)

    # 股票基本資料（名稱 / 產業別），量小直接整批更新
    try:
        info_rows_all = fetch_stock_info(finmind_token)
        info_rows = [r for r in info_rows_all if r["stock_id"] in set(stock_ids)]
        written_info = d1_upsert(
            db,
            "stock_info",
            ["stock_id", "stock_name", "industry_category"],
            info_rows,
            ["stock_id"],
        )
        print(f"[info] stock_info 寫入 {written_info} 筆")
    except Exception as err:  # noqa: BLE001
        print(f"[warn] 抓取 TaiwanStockInfo 失敗，略過股票名稱更新：{err}", file=sys.stderr)

    db.query(
        "INSERT INTO fetch_log (date, row_count, status, message) VALUES (?, ?, 'ok', NULL) "
        "ON CONFLICT(date) DO UPDATE SET row_count=excluded.row_count, status='ok', message=NULL, "
        "fetched_at=datetime('now');",
        [target_date, written],
    )

    print(f"[done] {target_date} 資料處理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
