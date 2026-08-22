#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八大官股行庫買賣追蹤 - 資料抓取腳本 (HiStock 網頁 -> Cloudflare D1)

【只給私人使用！】
這支腳本抓的是 histock.tw 網頁上公開顯示的「八大官股銀行/公股行庫買賣超排名」表格，
不是官方 API。HiStock 服務條款第 21 條「不得為商業利用」、第 14 條智慧財產權都限制
「重製 / 公開傳輸 / 發表」其網站內容。這支腳本只適合你自己私下抓來看、私下分析，
**不要**把抓下來的資料透過 govbank.pages.dev 這個公開網站對外展示，也不要拿來做
商業用途。如果你想要一個可以正大光明公開展示的版本，還是建議走 FinMind Sponsor
方案（backend/fetch_data.py 那支）。

用途：
  每個交易日收盤後執行一次，從 histock.tw/stock/broker8.aspx 抓「今天」的
  Top 30 買超 + Top 30 賣超排名（每檔股票拆到 8 家官股銀行的買賣超金額），
  用 FinMind 免費資料集 TaiwanStockInfo 把股票名稱轉成股票代號，
  再把資料 upsert 進 Cloudflare D1（沿用跟 fetch_data.py 一樣的資料表）。

跟 FinMind 版本（fetch_data.py）的差異、資料粒度限制：
  1. 只有 Top 30 買超 + Top 30 賣超，不是「當日全部股票」明細
     （FinMind 版本理論上可以拿到全市場逐檔資料）。
  2. 只有「淨買賣超金額」（單位：萬元，這裡會換算成元），
     沒有拆買進 / 賣出各自的股數與金額，所以 gov_bank_daily.buy / sell（股數）
     這兩欄會固定寫 0，buy_amount / sell_amount 是用淨額的正負號拆出來的
     （淨買超 -> 全部算進 buy_amount；淨賣超 -> 全部算進 sell_amount，
     不代表真的完全沒有反向交易，只是網頁沒有提供更細的拆法）。
  3. 股票名稱 -> 股票代號是用 FinMind 免費的 TaiwanStockInfo 資料集做字串對照，
     少數名稱（例如 ETF 全名、"國巨*" 這種帶星號的）可能對不到，
     對不到的話 stock_id 會 fallback 用股票名稱本身代替，並印出警告，
     你可以之後回來手動修正 stock_info 表或調整 NAME_ALIAS。

執行方式：
  python backend/fetch_histock.py                  # 抓「今天」(Asia/Taipei)
  python backend/fetch_histock.py --date 2026-08-21 # 指定日期寫入（抓到的仍是網頁當下顯示的最新一天）

需要的環境變數（GitHub Actions secrets，跟 fetch_data.py 共用）：
  FINMIND_TOKEN   FinMind token（只用來查免費的 TaiwanStockInfo / TaiwanStockPrice，不需要 Sponsor）
  CF_ACCOUNT_ID   Cloudflare 帳號 ID
  CF_DATABASE_ID  D1 資料庫 ID（govbank-db）
  CF_API_TOKEN    Cloudflare API Token，需有 D1 Edit 權限
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

HISTOCK_URL = "https://histock.tw/stock/broker8.aspx"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TAIPEI_TZ = timezone(timedelta(hours=8))

# HiStock 網頁上的 8 個官股銀行欄位順序（跟頁面表頭一致）
BANK_COLUMNS = ["合庫", "土銀", "台銀", "台企銀", "彰銀", "第一金", "兆豐銀", "華南永昌"]

# 少數 HiStock 顯示名稱跟 FinMind TaiwanStockInfo 的 stock_name 對不太起來時，
# 可以在這裡手動補對照（左邊 = HiStock 顯示名稱，右邊 = 股票代號）。
NAME_ALIAS: dict[str, str] = {
    # "國巨*": "2327",
}

D1_BATCH_SIZE = 200

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
}


# ---------------------------------------------------------------------------
# HiStock 網頁抓取
# ---------------------------------------------------------------------------


def parse_num(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip().replace(",", "")
    if t in ("", "-", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def extract_stock_list(ul) -> list[dict[str, Any]]:
    """解析一個 <ul class="stock-list"> 底下 30 個 <li>，回傳
    [{name, banks: [8個數字, 單位萬元], total}]"""
    rows = []
    for li in ul.find_all("li"):
        spans = li.find_all("span")
        if len(spans) < 12:
            continue
        name = spans[2].get_text(strip=True)
        nums = [parse_num(s.get_text()) for s in spans[3:12]]
        banks = nums[:8]
        total = nums[8] if len(nums) > 8 else None
        if not name:
            continue
        rows.append({"name": name, "banks": banks, "total": total})
    return rows


def fetch_histock_top30(retries: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """回傳 (買超Top30 list, 賣超Top30 list)。"""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(HISTOCK_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            uls = soup.select("ul.stock-list")
            if len(uls) < 2:
                raise RuntimeError(
                    f"預期在頁面上找到 2 個 ul.stock-list（買超/賣超），"
                    f"實際找到 {len(uls)} 個，網頁結構可能改了，要重新檢查"
                )
            buy_rows = extract_stock_list(uls[0])
            sell_rows = extract_stock_list(uls[1])
            if not buy_rows or not sell_rows:
                raise RuntimeError("解析出來的買超/賣超清單是空的，網頁結構可能改了")
            return buy_rows, sell_rows
        except Exception as err:  # noqa: BLE001
            last_err = err
            print(f"[warn] 抓取 HiStock 第 {attempt} 次失敗：{err}", file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"抓取 HiStock 失敗（已重試 {retries} 次）：{last_err}")


def rows_to_gov_bank_rows(
    date_str: str,
    buy_rows: list[dict[str, Any]],
    sell_rows: list[dict[str, Any]],
    name_to_id: dict[str, str],
) -> list[dict[str, Any]]:
    """把 HiStock 的 (股票, 8家銀行淨額) 轉成跟 gov_bank_daily 表一致的列
    （每個 stock_id + bank_name 一列）。"""
    out: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for source in (buy_rows, sell_rows):
        for row in source:
            name = row["name"]
            if name in seen_names:
                continue
            seen_names.add(name)

            stock_id = NAME_ALIAS.get(name) or name_to_id.get(name) or name
            if stock_id == name and name not in NAME_ALIAS:
                print(f"[warn] 股票名稱「{name}」在 FinMind TaiwanStockInfo 找不到對應代號，"
                      f"暫時用名稱本身當 stock_id，之後可在 NAME_ALIAS 裡補上", file=sys.stderr)

            for bank_name, net_amount_wan in zip(BANK_COLUMNS, row["banks"]):
                if net_amount_wan is None:
                    net_amount_wan = 0.0
                net_amount = int(round(net_amount_wan * 10000))  # 萬元 -> 元
                buy_amount = net_amount if net_amount > 0 else 0
                sell_amount = -net_amount if net_amount < 0 else 0
                out.append(
                    {
                        "date": date_str,
                        "stock_id": stock_id,
                        "bank_name": bank_name,
                        "buy": 0,
                        "sell": 0,
                        "buy_amount": buy_amount,
                        "sell_amount": sell_amount,
                    }
                )
    return out


# ---------------------------------------------------------------------------
# FinMind（只拿免費資料集：股票基本資料 + 收盤價，不需要 Sponsor）
# ---------------------------------------------------------------------------


def finmind_get(dataset: str, token: str, start_date: str, end_date: str,
                 retries: int = 3) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "token": token,
    }
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


def fetch_stock_info_map(token: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """回傳 (股票名稱 -> 股票代號 的 dict, 給 D1 stock_info 表用的 rows)。"""
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    rows = finmind_get("TaiwanStockInfo", token, "2000-01-01", today)
    name_to_id: dict[str, str] = {}
    dedup: dict[str, dict[str, Any]] = {}
    for r in rows:
        sid = str(r.get("stock_id", ""))
        name = r.get("stock_name", "")
        if not sid or not name:
            continue
        name_to_id[name] = sid
        dedup[sid] = {
            "stock_id": sid,
            "stock_name": name,
            "industry_category": r.get("industry_category", ""),
        }
    return name_to_id, list(dedup.values())


def fetch_stock_price(token: str, date_str: str, stock_ids: set[str]) -> list[dict[str, Any]]:
    rows = finmind_get("TaiwanStockPrice", token, date_str, date_str)
    cleaned = []
    for r in rows:
        sid = str(r.get("stock_id", ""))
        if sid not in stock_ids:
            continue
        cleaned.append(
            {
                "date": r.get("date", date_str),
                "stock_id": sid,
                "close": r.get("close"),
                "spread": r.get("spread"),
                "trading_volume": r.get("Trading_Volume"),
            }
        )
    return cleaned


# ---------------------------------------------------------------------------
# Cloudflare D1 (HTTP API) —— 跟 fetch_data.py 完全一樣
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
    parser = argparse.ArgumentParser(description="抓取 HiStock 八大官股行庫買賣超（Top30）寫入 D1")
    parser.add_argument(
        "--date",
        help="要寫入 D1 的日期 (YYYY-MM-DD)，預設台北時區的今天；"
             "注意：HiStock 網頁本身只顯示「最新一天」，這個參數只影響寫入 D1 時標記的日期，"
             "不會讓網頁回傳過去的資料",
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

    print(f"[info] 開始抓取 HiStock 八大官股行庫買賣超 Top30（寫入日期：{target_date}）")
    print("[warn] 此腳本抓的是 histock.tw 網頁公開內容，只供私人使用，"
          "不要透過公開網站對外展示、不要商業使用", file=sys.stderr)

    try:
        buy_rows, sell_rows = fetch_histock_top30()
    except Exception as err:  # noqa: BLE001
        print(f"[error] 抓取 HiStock 失敗：{err}", file=sys.stderr)
        db.query(
            "INSERT INTO fetch_log (date, row_count, status, message) VALUES (?, 0, 'error', ?) "
            "ON CONFLICT(date) DO UPDATE SET row_count=0, status='error', message=excluded.message, "
            "fetched_at=datetime('now');",
            [target_date, f"[histock] {err}"],
        )
        return 1

    print(f"[info] 買超 Top{len(buy_rows)}、賣超 Top{len(sell_rows)}")

    try:
        name_to_id, stock_info_rows = fetch_stock_info_map(finmind_token)
    except Exception as err:  # noqa: BLE001
        print(f"[warn] 抓取 TaiwanStockInfo 失敗，股票名稱->代號對照會全部 fallback 用名稱：{err}",
              file=sys.stderr)
        name_to_id, stock_info_rows = {}, []

    gov_bank_rows = rows_to_gov_bank_rows(target_date, buy_rows, sell_rows, name_to_id)
    stock_ids = sorted({r["stock_id"] for r in gov_bank_rows})
    print(f"[info] 整理出 {len(gov_bank_rows)} 筆買賣明細，涉及 {len(stock_ids)} 檔股票（Top30 買超 + Top30 賣超）")

    written = d1_upsert(
        db,
        "gov_bank_daily",
        ["date", "stock_id", "bank_name", "buy", "sell", "buy_amount", "sell_amount"],
        gov_bank_rows,
        ["date", "stock_id", "bank_name"],
    )
    print(f"[info] gov_bank_daily 寫入 {written} 筆")

    if stock_info_rows:
        info_rows = [r for r in stock_info_rows if r["stock_id"] in set(stock_ids)]
        written_info = d1_upsert(
            db, "stock_info", ["stock_id", "stock_name", "industry_category"],
            info_rows, ["stock_id"],
        )
        print(f"[info] stock_info 寫入 {written_info} 筆")

    try:
        price_rows = fetch_stock_price(finmind_token, target_date, set(stock_ids))
        written_price = d1_upsert(
            db, "stock_price_daily", ["date", "stock_id", "close", "spread", "trading_volume"],
            price_rows, ["date", "stock_id"],
        )
        print(f"[info] stock_price_daily 寫入 {written_price} 筆")
    except Exception as err:  # noqa: BLE001
        print(f"[warn] 抓取 TaiwanStockPrice 失敗，略過收盤價：{err}", file=sys.stderr)

    db.query(
        "INSERT INTO fetch_log (date, row_count, status, message) VALUES (?, ?, 'ok', ?) "
        "ON CONFLICT(date) DO UPDATE SET row_count=excluded.row_count, status='ok', "
        "message=excluded.message, fetched_at=datetime('now');",
        [target_date, written, "source=histock.tw, top30 only, private use"],
    )

    print(f"[done] {target_date} 資料處理完成（來源：histock.tw，僅私人使用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
