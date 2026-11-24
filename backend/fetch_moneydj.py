#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八大官股行庫買賣追蹤 - 資料抓取腳本 (MoneyDJ 券商進出排行 -> Cloudflare D1)

【只給私人分析使用】
資料來自富邦 e 點通 MoneyDJ「券商進出排行」網頁（非官方 API），抓來自己分析即可，
不要透過公開網站對外展示、不要商業使用。

資料來源：
  https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm?a=<券商>&b=<分點>&c=<E|B>&e=<起>&f=<迄>
    c=E -> 單位：張數（買進張數 / 賣出張數 / 差額）
    c=B -> 單位：仟元（買進金額 / 賣出金額 / 差額）
  每個頁面有「買超 Top50」+「賣超 Top50」兩張表，股票欄位的原始 HTML 是
  <script>GenLink2stk('AS2330','台積電');</script>，可直接取得股票代號。
  非交易日會回傳空的「資料日期」且 0 列。

跟 HiStock 版本（fetch_histock.py）比較：
  + 有股票代號，不用再拿名稱去對照
  + 買進 / 賣出分開，有張數也有金額
  + 可以指定任一天 -> 能補歷史資料（下拉選單目前可選到民國 114 年）
  - 每家券商只有買超、賣超各 Top50，不是全市場逐檔明細
  - 張數 Top50 跟金額 Top50 的股票集合不一定相同：只出現在其中一邊的股票，
    另一邊用當日收盤價估算（收盤價來自證交所＋櫃買中心；抓不到就填 0）

執行方式：
  python backend/fetch_moneydj.py                               # 抓今天 (Asia/Taipei)
  python backend/fetch_moneydj.py --date 2026-11-20              # 抓指定日
  python backend/fetch_moneydj.py --start 2026-10-01 --end 2026-11-20   # 補歷史（逐日、自動跳過假日）
  python backend/fetch_moneydj.py --date 2026-11-20 --dry-run    # 不寫 D1，只印結果（本機測試用）
  python backend/fetch_moneydj.py --include-branches             # 連各分點一起加總（請求數會變多）

環境變數：
  CF_ACCOUNT_ID / CF_DATABASE_ID / CF_API_TOKEN   寫入 D1 用（--dry-run 不需要）
  FINMIND_TOKEN   選填，收盤價改抓證交所＋櫃買中心官方資料，兩者都失敗才用 FinMind
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

MONEYDJ_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TAIPEI_TZ = timezone(timedelta(hours=8))

# (MoneyDJ 券商代號, 寫進 D1 的 bank_name) —— bank_name 沿用 fetch_histock.py 的寫法，
# 兩個來源混用時同一家銀行才會被歸在一起。
BROKERS: list[tuple[str, str]] = [
    ("1020", "合庫"),
    ("1030", "土銀"),
    ("1040", "台銀"),
    ("1110", "台企銀"),
    ("1230", "彰銀"),
    ("5380", "第一金"),
    ("7000", "兆豐銀"),
    ("9300", "華南永昌"),
]

REQUEST_INTERVAL = 0.8  # 每個請求之間間隔秒數，別打太快
D1_BATCH_SIZE = 200

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": MONEYDJ_URL,
}

RE_STOCK = re.compile(r"GenLink2stk\(\s*'[A-Z]*(\d[0-9A-Z]*)'\s*,\s*'([^']*)'\s*\)")
RE_STOCK_FALLBACK = re.compile(r"Link2Stk\(\s*'(\d[0-9A-Z]*)'\s*\)\s*;?\s*\"?\s*>\s*\d[0-9A-Z]*([^<]*)<")
RE_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
RE_NUM_TD = re.compile(r"<td[^>]*class=\"?t3n\d\"?[^>]*>(.*?)</td>", re.S | re.I)
RE_DATA_DATE = re.compile(r"資料日期[：:]\s*(\d{8})?")
RE_BRANCH_SELECT = re.compile(r"<select[^>]*sel_BrokerBranch[^>]*>(.*?)</select>", re.S | re.I)
RE_OPTION = re.compile(r"<option[^>]*value=\"?([^\" >]+)\"?[^>]*>([^<]*)", re.I)

_session = requests.Session()
_session.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# MoneyDJ
# ---------------------------------------------------------------------------


def _to_int(text: str) -> int:
    t = re.sub(r"<[^>]+>", "", text).replace(",", "").replace("&nbsp;", "").strip()
    try:
        return int(float(t))
    except ValueError:
        return 0


def fetch_html(broker: str, branch: str, unit: str, day: str, retries: int = 3) -> str:
    """unit: 'E' = 張數, 'B' = 仟元。day: YYYY-MM-DD"""
    params = {"a": broker, "b": branch, "c": unit, "e": day, "f": day}
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(MONEYDJ_URL, params=params, timeout=30)
            resp.raise_for_status()
            time.sleep(REQUEST_INTERVAL)
            return resp.content.decode("cp950", errors="replace")
        except Exception as err:  # noqa: BLE001
            last_err = err
            print(f"[warn] MoneyDJ {broker}/{branch}/{unit}/{day} 第 {attempt} 次失敗：{err}",
                  file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"MoneyDJ 抓取失敗（已重試 {retries} 次）：{last_err}")


def parse_page(html: str) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """回傳 (資料日期 YYYY-MM-DD 或 None, {stock_id: {name, buy, sell}})。
    buy / sell 的單位就是頁面單位（張 或 仟元），買超表跟賣超表合併。"""
    m = RE_DATA_DATE.search(html)
    data_date = None
    if m and m.group(1):
        d = m.group(1)
        data_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"

    rows: dict[str, dict[str, Any]] = {}
    for tr in RE_ROW.findall(html):
        if "t4t1" not in tr:
            continue
        sm = RE_STOCK.search(tr) or RE_STOCK_FALLBACK.search(tr)
        if not sm:
            continue
        nums = RE_NUM_TD.findall(tr)
        if len(nums) < 2:
            continue
        sid, name = sm.group(1), sm.group(2).strip()
        rows[sid] = {"name": name, "buy": _to_int(nums[0]), "sell": _to_int(nums[1])}
    return data_date, rows


def discover_branches(broker: str, day: str) -> list[str]:
    """從總公司頁面的「分點」下拉選單取得所有分點代號（含總公司本身）。"""
    html = fetch_html(broker, broker, "E", day)
    m = RE_BRANCH_SELECT.search(html)
    codes = [v for v, _ in RE_OPTION.findall(m.group(1))] if m else []
    if not codes:
        print(f"[warn] 找不到 {broker} 的分點清單（可能是用 JS 產生），只抓總公司", file=sys.stderr)
        return [broker]
    return codes


def fetch_broker_day(broker: str, day: str, include_branches: bool
                     ) -> tuple[bool, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """回傳 (當天是否有資料, 張數 {sid: {name,buy,sell}}, 仟元 {sid: {name,buy,sell}})，
    有 include_branches 時把所有分點加總。"""
    branches = discover_branches(broker, day) if include_branches else [broker]
    lots: dict[str, dict[str, Any]] = {}
    amts: dict[str, dict[str, Any]] = {}
    has_data = False
    for br in branches:
        for unit, bucket in (("E", lots), ("B", amts)):
            data_date, rows = parse_page(fetch_html(broker, br, unit, day))
            if data_date != day:
                # 非交易日 / 資料還沒出來：頁面日期是空的或不是要的那天
                continue
            has_data = has_data or bool(rows)
            for sid, r in rows.items():
                cur = bucket.setdefault(sid, {"name": r["name"], "buy": 0, "sell": 0})
                cur["buy"] += r["buy"]
                cur["sell"] += r["sell"]
    return has_data, lots, amts


def merge_rows(day: str, bank_name: str,
               lots: dict[str, dict[str, Any]], amts: dict[str, dict[str, Any]],
               close_map: dict[str, float]) -> tuple[list[dict[str, Any]], int]:
    """合併張數表與金額表成 gov_bank_daily 的列（buy/sell=股數、amount=元）。
    只出現在一邊的股票用收盤價估另一邊。回傳 (rows, 估算筆數)。"""
    out: list[dict[str, Any]] = []
    estimated = 0
    for sid in sorted(set(lots) | set(amts)):
        lot = lots.get(sid)
        amt = amts.get(sid)
        close = close_map.get(sid)
        if lot and amt:
            buy_sh, sell_sh = lot["buy"] * 1000, lot["sell"] * 1000
            buy_amt, sell_amt = amt["buy"] * 1000, amt["sell"] * 1000
        elif lot:
            buy_sh, sell_sh = lot["buy"] * 1000, lot["sell"] * 1000
            buy_amt = int(round(buy_sh * close)) if close else 0
            sell_amt = int(round(sell_sh * close)) if close else 0
            estimated += 1
        else:
            buy_amt, sell_amt = amt["buy"] * 1000, amt["sell"] * 1000
            buy_sh = int(round(buy_amt / close / 1000)) * 1000 if close else 0
            sell_sh = int(round(sell_amt / close / 1000)) * 1000 if close else 0
            estimated += 1
        out.append({
            "date": day, "stock_id": sid, "bank_name": bank_name,
            "buy": buy_sh, "sell": sell_sh,
            "buy_amount": buy_amt, "sell_amount": sell_amt,
        })
    return out, estimated


# ---------------------------------------------------------------------------
# FinMind（選填：只抓免費的收盤價）
# ---------------------------------------------------------------------------


TWSE_PRICE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_PRICE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


def _num(text: Any) -> float | None:
    t = re.sub(r"<[^>]+>", "", str(text or "")).replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _get_json(url: str, params: dict[str, str], retries: int = 3) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as err:  # noqa: BLE001
            last_err = err
            time.sleep(3 * attempt)
    raise RuntimeError(f"{url} 失敗：{last_err}")


def fetch_price_twse(day: str) -> list[dict[str, Any]]:
    """證交所每日收盤行情（上市，含 ETF，不含權證）"""
    j = _get_json(TWSE_PRICE_URL, {"date": day.replace("-", ""), "type": "ALLBUT0999",
                                   "response": "json"})
    out = []
    for t in j.get("tables") or []:
        fields = t.get("fields") or []
        if "證券代號" not in fields or "收盤價" not in fields:
            continue
        i_id, i_close = fields.index("證券代號"), fields.index("收盤價")
        i_sign, i_diff = fields.index("漲跌(+/-)"), fields.index("漲跌價差")
        i_vol = fields.index("成交股數")
        for d in t.get("data") or []:
            close = _num(d[i_close])
            if close is None:
                continue
            diff = _num(d[i_diff]) or 0.0
            if "-" in re.sub(r"<[^>]+>", "", str(d[i_sign])):
                diff = -diff
            out.append({"date": day, "stock_id": str(d[i_id]).strip(), "close": close,
                        "spread": diff, "trading_volume": int(_num(d[i_vol]) or 0)})
    return out


def fetch_price_tpex(day: str) -> list[dict[str, Any]]:
    """櫃買中心上櫃股票行情"""
    j = _get_json(TPEX_PRICE_URL, {"date": day.replace("-", "/"), "type": "EW",
                                   "response": "json"})
    out = []
    for t in j.get("tables") or []:
        fields = [f.strip() for f in (t.get("fields") or [])]
        if "代號" not in fields or "收盤" not in fields:
            continue
        i_id, i_close, i_diff = fields.index("代號"), fields.index("收盤"), fields.index("漲跌")
        i_vol = fields.index("成交股數")
        for d in t.get("data") or []:
            close = _num(d[i_close])
            if close is None:
                continue
            out.append({"date": day, "stock_id": str(d[i_id]).strip(), "close": close,
                        "spread": _num(d[i_diff]) or 0.0,
                        "trading_volume": int(_num(d[i_vol]) or 0)})
    return out


def fetch_stock_price(token: str | None, day: str) -> list[dict[str, Any]]:
    """收盤價：證交所 + 櫃買中心官方免費資料；都失敗才試 FinMind。"""
    rows: list[dict[str, Any]] = []
    for name, fn in (("證交所", fetch_price_twse), ("櫃買中心", fetch_price_tpex)):
        try:
            got = fn(day)
            print(f"[info] {name}收盤價 {len(got)} 檔")
            rows.extend(got)
        except Exception as err:  # noqa: BLE001
            print(f"[warn] {name}收盤價失敗：{err}", file=sys.stderr)
    if rows or not token:
        return rows
    params = {"dataset": "TaiwanStockPrice", "start_date": day, "end_date": day, "token": token}
    try:
        resp = requests.get(FINMIND_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as err:  # noqa: BLE001
        print(f"[warn] FinMind TaiwanStockPrice 失敗，略過收盤價：{err}", file=sys.stderr)
        return []
    return [{
        "date": r.get("date", day),
        "stock_id": str(r.get("stock_id", "")),
        "close": r.get("close"),
        "spread": r.get("spread"),
        "trading_volume": r.get("Trading_Volume"),
    } for r in data]


# ---------------------------------------------------------------------------
# Cloudflare D1 (HTTP API) —— 跟 fetch_data.py / fetch_histock.py 一樣
# ---------------------------------------------------------------------------


class D1Client:
    def __init__(self, account_id: str, database_id: str, api_token: str):
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self.headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    def query(self, sql: str, params: list[Any] | None = None) -> Any:
        resp = requests.post(self.base_url, headers=self.headers,
                             json={"sql": sql, "params": params or []}, timeout=60)
        if not resp.ok:
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
    # D1 HTTP API 每個 query 最多綁 100 個參數
    batch_size = max(1, min(D1_BATCH_SIZE, 100 // len(columns)))
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        one = "(" + ", ".join(["?"] * len(columns)) + ")"
        sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES {', '.join([one] * len(chunk))} "
               f"ON CONFLICT({', '.join(conflict_cols)}) DO UPDATE SET {set_clause};")
        db.query(sql, [row.get(c) for row in chunk for c in columns])
        total += len(chunk)
    return total


def log_fetch(db: D1Client | None, day: str, count: int, status: str, message: str | None) -> None:
    if db is None:
        return
    db.query(
        "INSERT INTO fetch_log (date, row_count, status, message) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET row_count=excluded.row_count, status=excluded.status, "
        "message=excluded.message, fetched_at=datetime('now');",
        [day, count, status, message],
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def process_day(day: str, db: D1Client | None, finmind_token: str | None,
                include_branches: bool) -> int:
    print(f"[info] === {day} ===")
    price_rows = fetch_stock_price(finmind_token, day)
    close_map = {r["stock_id"]: float(r["close"]) for r in price_rows if r.get("close")}

    all_rows: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    any_data = False
    estimated_total = 0
    for broker, bank_name in BROKERS:
        try:
            has_data, lots, amts = fetch_broker_day(broker, day, include_branches)
        except Exception as err:  # noqa: BLE001
            print(f"[error] {bank_name}({broker}) 抓取失敗：{err}", file=sys.stderr)
            continue
        any_data = any_data or has_data
        rows, est = merge_rows(day, bank_name, lots, amts, close_map)
        estimated_total += est
        all_rows.extend(rows)
        for src in (lots, amts):
            for sid, r in src.items():
                names.setdefault(sid, r["name"])
        print(f"[info] {bank_name}({broker})：{len(rows)} 檔（其中 {est} 檔有一邊是用收盤價估算）")

    if not any_data:
        print(f"[warn] {day} 沒有資料（非交易日或資料尚未更新）")
        log_fetch(db, day, 0, "empty", "[moneydj] 無資料")
        return 0

    stock_ids = {r["stock_id"] for r in all_rows}
    print(f"[info] 合計 {len(all_rows)} 筆（{len(stock_ids)} 檔股票），估算 {estimated_total} 筆"
          + ("" if close_map else "；沒有收盤價，估算欄位填 0"))

    if db is None:
        # dry-run：印八家合計買賣超前 10 名
        agg: dict[str, int] = {}
        for r in all_rows:
            agg[r["stock_id"]] = agg.get(r["stock_id"], 0) + r["buy_amount"] - r["sell_amount"]
        top = sorted(agg.items(), key=lambda x: x[1], reverse=True)
        print("[dry-run] 八家合計買超前 10（萬元）：")
        for sid, v in top[:10]:
            print(f"  {sid} {names.get(sid, ''):<10} {v / 10000:>12,.0f}")
        print("[dry-run] 八家合計賣超前 10（萬元）：")
        for sid, v in top[-10:][::-1]:
            print(f"  {sid} {names.get(sid, ''):<10} {v / 10000:>12,.0f}")
        return len(all_rows)

    written = d1_upsert(
        db, "gov_bank_daily",
        ["date", "stock_id", "bank_name", "buy", "sell", "buy_amount", "sell_amount"],
        all_rows, ["date", "stock_id", "bank_name"],
    )
    # 只更新股票名稱，不動既有的產業別
    d1_upsert(db, "stock_info", ["stock_id", "stock_name"],
              [{"stock_id": s, "stock_name": n} for s, n in names.items()], ["stock_id"])
    d1_upsert(db, "stock_price_daily", ["date", "stock_id", "close", "spread", "trading_volume"],
              [r for r in price_rows if r["stock_id"] in stock_ids], ["date", "stock_id"])
    log_fetch(db, day, written, "ok",
              f"source=moneydj, top50/broker, estimated={estimated_total}"
              + (", branches" if include_branches else ""))
    print(f"[info] gov_bank_daily 寫入 {written} 筆")
    return written


def daterange(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # 週末直接跳過，國定假日交給頁面判斷
            yield d.isoformat()
        d += timedelta(days=1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="抓取 MoneyDJ 八大官股券商進出（Top50）寫入 D1")
    p.add_argument("--date", help="單一交易日 YYYY-MM-DD，預設台北時區今天")
    p.add_argument("--start", help="補資料起日 YYYY-MM-DD（搭配 --end）")
    p.add_argument("--end", help="補資料迄日 YYYY-MM-DD，預設今天")
    p.add_argument("--include-branches", action="store_true", help="加總各分點（請求數變多）")
    p.add_argument("--dry-run", action="store_true", help="不寫 D1，只印結果")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    today = datetime.now(TAIPEI_TZ).date()

    if args.start:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end) if args.end else today
        days = list(daterange(start, end))
    else:
        days = [args.date or os.environ.get("FETCH_DATE") or today.isoformat()]

    db: D1Client | None = None
    if not args.dry_run:
        env = {k: os.environ.get(k) for k in ("CF_ACCOUNT_ID", "CF_DATABASE_ID", "CF_API_TOKEN")}
        missing = [k for k, v in env.items() if not v]
        if missing:
            print(f"[error] 缺少環境變數：{', '.join(missing)}（或加 --dry-run）", file=sys.stderr)
            return 1
        db = D1Client(env["CF_ACCOUNT_ID"], env["CF_DATABASE_ID"], env["CF_API_TOKEN"])

    finmind_token = os.environ.get("FINMIND_TOKEN") or None
    print(f"[info] MoneyDJ 來源，共 {len(days)} 天；僅供私人分析使用", file=sys.stderr)

    failed = 0
    for day in days:
        try:
            process_day(day, db, finmind_token, args.include_branches)
        except Exception as err:  # noqa: BLE001
            failed += 1
            print(f"[error] {day} 失敗：{err}", file=sys.stderr)
            try:
                log_fetch(db, day, 0, "error", f"[moneydj] {err}")
            except Exception:  # noqa: BLE001
                pass
    print(f"[done] 完成 {len(days) - failed}/{len(days)} 天")
    return 1 if failed and failed == len(days) else 0


if __name__ == "__main__":
    sys.exit(main())
