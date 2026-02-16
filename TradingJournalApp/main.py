from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
STATIC_DIR = BASE_DIR / "static"
TRADING_JOURNAL_PATH = DATA_DIR / "trading_journal.csv"
TEMP_TRADES_PATH = TEMP_DIR / "trades.csv"
REQUIRED_UPLOAD_NAME = "tradebook-OI8327-EQ.csv"
REQUIRED_UPLOAD_COLUMNS = [
    "symbol",
    "isin",
    "trade_date",
    "quantity",
    "price",
    "order_execution_time",
    "order_id",
    "trade_type",
]
JOURNAL_COLUMNS = [
    "Symbol",
    "Buy Date",
    "Buy Time",
    "Buy Quantity",
    "Buy Price",
    "Stop Loss",
    "Set up",
    "Sell Date",
    "Sell Time",
    "Sell Quantity",
    "Open Quantity",
    "Total Buy Price",
    "Total Sell Price",
    "Total Tax",
    "PnL",
    "PnL Pct",
    "Trade Status",
    "Trade Outcome",
    "Days in Trade",
    "Order_Id",
]

app = FastAPI(title="TradingJournalApp")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADING_JOURNAL_PATH.exists() or TRADING_JOURNAL_PATH.stat().st_size == 0:
        pd.DataFrame(columns=JOURNAL_COLUMNS).to_csv(TRADING_JOURNAL_PATH, index=False)


def load_journal() -> pd.DataFrame:
    ensure_storage()
    try:
        df = pd.read_csv(TRADING_JOURNAL_PATH)
    except Exception:
        return pd.DataFrame(columns=JOURNAL_COLUMNS)
    for col in JOURNAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[JOURNAL_COLUMNS].copy()


def save_journal(df: pd.DataFrame) -> None:
    for col in JOURNAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[JOURNAL_COLUMNS].to_csv(TRADING_JOURNAL_PATH, index=False)


def to_float(value: object) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def parse_order_ids(value: object) -> set[str]:
    if value is None or pd.isna(value):
        return set()
    raw = str(value).strip()
    if not raw:
        return set()
    return {v for v in raw.split("|") if v}


def all_seen_order_ids(journal: pd.DataFrame) -> set[str]:
    ids: set[str] = set()
    if journal.empty:
        return ids
    for item in journal["Order_Id"].tolist():
        ids.update(parse_order_ids(item))
    return ids


def add_order_id(existing: object, order_id: str) -> str:
    ids = parse_order_ids(existing)
    ids.add(order_id)
    return "|".join(sorted(ids))


def tax_from_value(total_buy: float, total_sell: float) -> float:
    return round((total_buy + total_sell) * 0.001, 2)


def calc_days_in_trade(row: pd.Series) -> int:
    buy_date = pd.to_datetime(row.get("Buy Date"), errors="coerce")
    sell_date = pd.to_datetime(row.get("Sell Date"), errors="coerce")
    if pd.isna(buy_date):
        return 0
    if pd.isna(sell_date):
        sell_date = pd.Timestamp(date.today())
    return int(max((sell_date - buy_date).days, 0))


def recalc_row(row: pd.Series) -> pd.Series:
    buy_qty = to_float(row.get("Buy Quantity"))
    sell_qty = to_float(row.get("Sell Quantity"))
    buy_price = to_float(row.get("Buy Price"))
    sell_price = to_float(row.get("Sell Price"))

    open_qty = max(round(buy_qty - sell_qty, 2), 0.0)
    total_buy = round(buy_qty * buy_price, 2)
    total_sell = round(sell_qty * sell_price, 2)
    total_tax = tax_from_value(total_buy, total_sell)
    pnl = round(total_sell - total_buy - total_tax, 2)
    pnl_pct = round((pnl / total_buy) * 100, 2) if total_buy else 0.0

    row["Open Quantity"] = open_qty
    row["Total Buy Price"] = total_buy
    row["Total Sell Price"] = total_sell
    row["Total Tax"] = total_tax
    row["PnL"] = pnl
    row["PnL Pct"] = pnl_pct
    row["Trade Status"] = "Open" if open_qty > 0 else "Closed"
    row["Trade Outcome"] = "Win" if pnl > 0 else "Loss" if pnl < 0 else "Breakeven"
    row["Days in Trade"] = calc_days_in_trade(row)
    return row


def _time_string(value: object) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%H:%M:%S")


def process_tradebook(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0.0)
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["trade_time"] = df["order_execution_time"].apply(_time_string)

    grouped = (
        df.groupby(["order_id", "trade_type"], dropna=False)
        .agg(
            symbol=("symbol", "first"),
            trade_date=("trade_date", "first"),
            trade_time=("trade_time", "first"),
            quantity=("quantity", "sum"),
            weighted_sum=("price", lambda s: 0.0),
        )
        .reset_index()
    )

    weights = df.assign(weighted=df["quantity"] * df["price"]).groupby(["order_id", "trade_type"], dropna=False).agg(
        weighted=("weighted", "sum"),
        qty=("quantity", "sum"),
    )
    weights["price"] = weights.apply(lambda r: (r["weighted"] / r["qty"]) if r["qty"] else 0.0, axis=1)
    weights = weights.reset_index()[["order_id", "trade_type", "price"]]

    grouped = grouped.merge(weights, on=["order_id", "trade_type"], how="left")
    grouped["quantity"] = grouped["quantity"].round(2)
    grouped["price"] = grouped["price"].fillna(0.0).round(2)
    grouped["trade_value"] = (grouped["quantity"] * grouped["price"]).round(2)

    return grouped[
        [
            "symbol",
            "trade_date",
            "trade_time",
            "trade_type",
            "quantity",
            "price",
            "trade_value",
            "order_id",
        ]
    ]


def apply_trade_to_journal(journal: pd.DataFrame, trade: pd.Series) -> pd.DataFrame:
    symbol = str(trade.get("symbol", ""))
    order_id = str(trade.get("order_id", ""))
    trade_type = str(trade.get("trade_type", "")).upper()
    qty = to_float(trade.get("quantity"))
    price = to_float(trade.get("price"))
    tdate = str(trade.get("trade_date", ""))
    ttime = str(trade.get("trade_time", ""))

    if order_id in all_seen_order_ids(journal):
        return journal

    symbol_rows = journal[journal["Symbol"].astype(str) == symbol]
    open_rows = symbol_rows[symbol_rows["Trade Status"].astype(str).str.lower() == "open"]
    target_idx: Optional[int] = int(open_rows.index[0]) if len(open_rows) > 0 else None

    if target_idx is None:
        new_row = {col: "" for col in JOURNAL_COLUMNS}
        new_row["Symbol"] = symbol
        new_row["Order_Id"] = order_id
        new_row["Set up"] = ""
        new_row["Stop Loss"] = ""
        if trade_type == "BUY":
            new_row["Buy Date"] = tdate
            new_row["Buy Time"] = ttime
            new_row["Buy Quantity"] = qty
            new_row["Buy Price"] = price
            new_row["Sell Quantity"] = 0
            new_row["Sell Price"] = 0
        else:
            new_row["Buy Quantity"] = 0
            new_row["Buy Price"] = 0
            new_row["Sell Date"] = tdate
            new_row["Sell Time"] = ttime
            new_row["Sell Quantity"] = qty
            new_row["Sell Price"] = price
        journal = pd.concat([journal, pd.DataFrame([new_row])], ignore_index=True)
        target_idx = int(journal.index[-1])
    else:
        row = journal.loc[target_idx].copy()
        if trade_type == "BUY":
            old_qty = to_float(row.get("Buy Quantity"))
            old_price = to_float(row.get("Buy Price"))
            new_qty = old_qty + qty
            row["Buy Quantity"] = round(new_qty, 2)
            row["Buy Price"] = round(((old_qty * old_price) + (qty * price)) / new_qty, 2) if new_qty else 0
            if not str(row.get("Buy Date", "")).strip():
                row["Buy Date"] = tdate
            if not str(row.get("Buy Time", "")).strip():
                row["Buy Time"] = ttime
        elif trade_type == "SELL":
            old_qty = to_float(row.get("Sell Quantity"))
            old_price = to_float(row.get("Sell Price"))
            new_qty = old_qty + qty
            row["Sell Quantity"] = round(new_qty, 2)
            row["Sell Price"] = round(((old_qty * old_price) + (qty * price)) / new_qty, 2) if new_qty else 0
            row["Sell Date"] = tdate
            row["Sell Time"] = ttime
        row["Order_Id"] = add_order_id(row.get("Order_Id"), order_id)
        journal.loc[target_idx] = row

    journal.loc[target_idx] = recalc_row(journal.loc[target_idx].copy())
    return journal


def load_open_trades() -> pd.DataFrame:
    df = load_journal()
    if df.empty:
        return df
    return df[df["Trade Status"].astype(str).str.lower() == "open"].copy()


def add_market_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    current_prices: list[float] = []
    for symbol in df["Symbol"].astype(str).tolist():
        ticker = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
        try:
            history = yf.Ticker(ticker).history(period="1d")
            price = float(history["Close"].iloc[-1]) if not history.empty else 0.0
        except Exception:
            price = 0.0
        current_prices.append(round(price, 2))

    out = df.copy()
    out["Current Price"] = current_prices
    out["Buy Quantity"] = pd.to_numeric(out["Buy Quantity"], errors="coerce").fillna(0)
    out["Open Quantity"] = pd.to_numeric(out["Open Quantity"], errors="coerce").fillna(0)
    out["Buy Price"] = pd.to_numeric(out["Buy Price"], errors="coerce").fillna(0)
    out["Stop Loss"] = pd.to_numeric(out["Stop Loss"], errors="coerce").fillna(0)
    out["Sell Quantity"] = pd.to_numeric(out["Sell Quantity"], errors="coerce").fillna(0)
    out["Sell Price"] = pd.to_numeric(out["Sell Price"], errors="coerce").fillna(0)
    out["Total Tax"] = pd.to_numeric(out["Total Tax"], errors="coerce").fillna(0)

    out["Booked PnL"] = (out["Sell Quantity"] * (out["Sell Price"] - out["Buy Price"]) - out["Total Tax"]).round(2)
    out["Open PnL"] = (out["Open Quantity"] * (out["Current Price"] - out["Buy Price"])).round(2)
    out["Open Risk"] = (out["Open Quantity"] * (out["Buy Price"] - out["Stop Loss"])).round(2)
    return out


def dashboard_stats(open_df: pd.DataFrame, all_df: pd.DataFrame) -> dict:
    now = pd.Timestamp.now()
    work = all_df.copy()
    work["Sell Date"] = pd.to_datetime(work["Sell Date"], errors="coerce")
    work["PnL"] = pd.to_numeric(work["PnL"], errors="coerce").fillna(0)

    month_mask = (work["Sell Date"].dt.year == now.year) & (work["Sell Date"].dt.month == now.month)
    quarter = (now.month - 1) // 3 + 1
    quarter_mask = (work["Sell Date"].dt.year == now.year) & (((work["Sell Date"].dt.month - 1) // 3 + 1) == quarter)
    year_mask = work["Sell Date"].dt.year == now.year

    return {
        "total_open_trades": int(len(open_df)),
        "risk_free_trades": int((open_df["Open Risk"] <= 0).sum()) if not open_df.empty else 0,
        "trades_with_open_risk": int((open_df["Open Risk"] > 0).sum()) if not open_df.empty else 0,
        "total_risk": round(float(open_df["Open Risk"].sum()) if not open_df.empty else 0.0, 2),
        "monthly_pnl": round(float(work.loc[month_mask, "PnL"].sum()), 2),
        "quarterly_pnl": round(float(work.loc[quarter_mask, "PnL"].sum()), 2),
        "annual_pnl": round(float(work.loc[year_mask, "PnL"].sum()), 2),
    }


def monthly_metrics(year_df: pd.DataFrame) -> pd.DataFrame:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    rows: list[dict] = []
    for m in range(1, 13):
        md = year_df[year_df["Sell Date"].dt.month == m]
        wins = md[md["PnL"] > 0]
        losses = md[md["PnL"] < 0]
        total = len(md)
        rows.append(
            {
                "month": months[m - 1],
                "Average Gain": round(float(wins["PnL"].mean()) if len(wins) else 0.0, 2),
                "Average Loss": round(float(losses["PnL"].mean()) if len(losses) else 0.0, 2),
                "WIN %": round((len(wins) / total * 100) if total else 0.0, 2),
                "Total Trades": int(total),
                "Largest Gain": round(float(wins["PnL"].max()) if len(wins) else 0.0, 2),
                "Largest Loss": round(float(losses["PnL"].min()) if len(losses) else 0.0, 2),
                "Average Days Gains": round(float(wins["Days in Trade"].mean()) if len(wins) else 0.0, 2),
                "Average Days Loss": round(float(losses["Days in Trade"].mean()) if len(losses) else 0.0, 2),
            }
        )
    return pd.DataFrame(rows)


def quarterly_from_monthly(monthly_df: pd.DataFrame) -> dict:
    quarter_map = {
        "Q1": ["Jan", "Feb", "Mar"],
        "Q2": ["Apr", "May", "Jun"],
        "Q3": ["Jul", "Aug", "Sep"],
        "Q4": ["Oct", "Nov", "Dec"],
    }
    result = {}

    def weighted_avg(chunk: pd.DataFrame, col: str) -> float:
        w = chunk["Total Trades"].astype(float)
        total_w = float(w.sum())
        if total_w == 0:
            return 0.0
        return float((chunk[col].astype(float) * w).sum() / total_w)

    for q, m_names in quarter_map.items():
        chunk = monthly_df[monthly_df["month"].isin(m_names)]
        win_pct = weighted_avg(chunk, "WIN %")
        avg_gain = weighted_avg(chunk, "Average Gain")
        avg_loss = weighted_avg(chunk, "Average Loss")
        wl_ratio = abs(avg_gain / avg_loss) if avg_loss else 0.0
        result[q] = {
            "Winning Percentage": round(win_pct, 2),
            "Average Gain": round(avg_gain, 2),
            "Average Loss": round(avg_loss, 2),
            "Win/Loss Ratio": round(wl_ratio, 2),
            "Adjusted Win/Loss Ratio": round(wl_ratio * (win_pct / 100), 2),
        }
    return result


@app.on_event("startup")
def startup_event() -> None:
    ensure_storage()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/import", status_code=302)


@app.get("/import")
def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request, "message": None})


@app.post("/import")
async def import_tradebook(request: Request, tradebook: UploadFile = File(...)):
    ensure_storage()
    if tradebook.filename != REQUIRED_UPLOAD_NAME:
        return templates.TemplateResponse("import.html", {"request": request, "message": "Invalid File"})

    try:
        uploaded = pd.read_csv(tradebook.file)
    except Exception:
        return templates.TemplateResponse("import.html", {"request": request, "message": "Invalid File"})

    if sorted(uploaded.columns.tolist()) != sorted(REQUIRED_UPLOAD_COLUMNS):
        return templates.TemplateResponse("import.html", {"request": request, "message": "Invalid File"})

    trades = process_tradebook(uploaded)
    trades.to_csv(TEMP_TRADES_PATH, index=False)

    journal = load_journal()
    for _, trade in trades.iterrows():
        journal = apply_trade_to_journal(journal, trade)

    save_journal(journal)
    if TEMP_TRADES_PATH.exists():
        TEMP_TRADES_PATH.unlink()

    message = f"Successfully processed the uploaded tradebook and found total {len(trades)} trades"
    return templates.TemplateResponse("import.html", {"request": request, "message": message})


@app.get("/open_trades")
def open_trades(request: Request):
    all_rows = load_journal()
    open_rows = add_market_data(load_open_trades())
    open_rows["row_index"] = open_rows.index.astype(int)
    stats = dashboard_stats(open_rows, all_rows)
    return templates.TemplateResponse(
        "open_trades.html",
        {
            "request": request,
            "rows": open_rows.fillna("").to_dict(orient="records"),
            "stats": stats,
        },
    )


@app.post("/update_trade")
def update_trade(
    row_index: int = Form(...),
    stop_loss: str = Form(""),
    setup: str = Form(""),
):
    journal = load_journal()
    if 0 <= row_index < len(journal):
        journal.at[row_index, "Stop Loss"] = to_float(stop_loss) if stop_loss else ""
        journal.at[row_index, "Set up"] = setup
        journal.loc[row_index] = recalc_row(journal.loc[row_index].copy())
        save_journal(journal)
    return RedirectResponse(url="/open_trades", status_code=303)


@app.get("/summary")
def summary(request: Request, year: Optional[int] = None):
    df = load_journal()
    selected_year = year or date.today().year

    df["Sell Date"] = pd.to_datetime(df["Sell Date"], errors="coerce")
    df["PnL"] = pd.to_numeric(df["PnL"], errors="coerce").fillna(0)
    df["Days in Trade"] = pd.to_numeric(df["Days in Trade"], errors="coerce").fillna(0)

    year_df = df[df["Sell Date"].dt.year == selected_year]
    monthly = monthly_metrics(year_df)
    quarterly = quarterly_from_monthly(monthly)

    year_opts = sorted({int(y) for y in df["Sell Date"].dt.year.dropna().tolist()} | {selected_year}, reverse=True)

    return templates.TemplateResponse(
        "summary.html",
        {
            "request": request,
            "selected_year": selected_year,
            "years": year_opts,
            "monthly_rows": monthly.to_dict(orient="records"),
            "quarterly": quarterly,
        },
    )


@app.get("/analytics")
def analytics(
    request: Request,
    start_date: str = "",
    end_date: str = "",
    symbol: str = "",
    trade_outcome: str = "",
    trade_status: str = "",
):
    df = load_journal()
    df["Buy Date"] = pd.to_datetime(df["Buy Date"], errors="coerce")
    df["PnL"] = pd.to_numeric(df["PnL"], errors="coerce").fillna(0)

    filtered = df.copy()
    if start_date:
        filtered = filtered[filtered["Buy Date"] >= pd.to_datetime(start_date)]
    if end_date:
        filtered = filtered[filtered["Buy Date"] <= pd.to_datetime(end_date)]
    if symbol:
        filtered = filtered[filtered["Symbol"].astype(str) == symbol]
    if trade_outcome:
        filtered = filtered[filtered["Trade Outcome"].astype(str) == trade_outcome]
    if trade_status:
        filtered = filtered[filtered["Trade Status"].astype(str) == trade_status]

    wins = filtered[filtered["PnL"] > 0]
    losses = filtered[filtered["PnL"] < 0]
    total_count = len(filtered)
    metrics = {
        "total_pnl": round(float(filtered["PnL"].sum()) if total_count else 0.0, 2),
        "win_pct": round((len(wins) / total_count * 100) if total_count else 0.0, 2),
        "average_gain": round(float(wins["PnL"].mean()) if len(wins) else 0.0, 2),
        "average_loss": round(float(losses["PnL"].mean()) if len(losses) else 0.0, 2),
    }

    chart_df = filtered.sort_values("Buy Date")
    chart_labels = chart_df["Buy Date"].dt.strftime("%Y-%m-%d").fillna("").tolist()
    chart_values = chart_df["PnL"].cumsum().tolist()
    symbols = sorted([s for s in df["Symbol"].dropna().astype(str).unique().tolist() if s])

    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "rows": filtered.fillna("").to_dict(orient="records"),
            "metrics": metrics,
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "symbols": symbols,
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "symbol": symbol,
                "trade_outcome": trade_outcome,
                "trade_status": trade_status,
            },
        },
    )
