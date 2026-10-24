"""The exchange's daily derivatives file (bhavcopy): every futures and options contract that traded, with its settlement price, volume and open interest.

Two layouts exist: the old `fo<DD><MON><YYYY>bhav.csv` (to 5 July 2024) and the newer `BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv` (from at least 28 June 2024). `parse` reads either into
plain rows and `reduce` keeps what the detectors use: the nearest two expiries of each stock's and index's futures; the open interest of each option kind by expiry (three nearest expiries);
and, for the nearest expiry only, the open interest by strike (only when asked for, because that is most of the file)."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from athena.contracts import SchemaChangedError

ARCHIVE = "https://nsearchives.nseindia.com/content"
OLD_UNTIL = date(2024, 7, 5)
FUTURES_EXPIRIES = 2
OPTION_EXPIRIES = 3
KINDS = {"FUTSTK": "FUTSTK", "FUTIDX": "FUTIDX", "OPTSTK": "OPTSTK", "OPTIDX": "OPTIDX", "STF": "FUTSTK", "IDF": "FUTIDX", "STO": "OPTSTK", "IDO": "OPTIDX"}
OLD_COLUMNS = {"instrument": "INSTRUMENT", "symbol": "SYMBOL", "expiry": "EXPIRY_DT", "strike": "STRIKE_PR", "option": "OPTION_TYP", "close": "CLOSE", "settle": "SETTLE_PR", "volume": "CONTRACTS", "oi": "OPEN_INT", "chg_oi": "CHG_IN_OI"}
NEW_COLUMNS = {"instrument": "FinInstrmTp", "symbol": "TckrSymb", "expiry": "XpryDt", "strike": "StrkPric", "option": "OptnTp", "close": "ClsPric", "settle": "SttlmPric", "volume": "TtlTradgVol", "oi": "OpnIntrst", "chg_oi": "ChngInOpnIntrst"}


@dataclass(frozen=True)
class Contract:
    instrument: str  # FUTSTK, FUTIDX, OPTSTK or OPTIDX
    symbol: str
    expiry: date
    strike: float
    option: str  # "CE", "PE" or "" for a future
    close: float
    settle: float
    volume: float
    oi: float
    chg_oi: float


def urls_for(day: date) -> list[str]:
    mon = day.strftime("%b").upper()
    old = f"{ARCHIVE}/historical/DERIVATIVES/{day:%Y}/{mon}/fo{day:%d}{mon}{day:%Y}bhav.csv.zip"
    new = f"{ARCHIVE}/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return [old, new] if day <= OLD_UNTIL else [new, old]


def _date(text: str) -> date:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise SchemaChangedError(f"a derivatives file holds a date this reader does not know: {text!r}")


def _number(text: str) -> float:
    try:
        return float(text) if text else 0.0
    except ValueError:
        raise SchemaChangedError(f"a derivatives file holds a value that is not a number: {text!r}") from None


def parse(text: str) -> list[Contract]:
    """Every contract of the four kinds in the file. An unknown header is a `SchemaChangedError`."""
    reader = csv.DictReader(io.StringIO(text))
    header = {name.strip() for name in reader.fieldnames or ()}
    columns = next((cols for cols in (OLD_COLUMNS, NEW_COLUMNS) if set(cols.values()) <= header), None)
    if columns is None:
        raise SchemaChangedError(f"unknown derivatives-file header: {sorted(header)[:8]}")
    out = []
    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        kind = KINDS.get(row[columns["instrument"]])
        if kind is None:
            continue
        option = row[columns["option"]]
        out.append(Contract(
            kind, row[columns["symbol"]], _date(row[columns["expiry"]]), _number(row[columns["strike"]]), option if option in ("CE", "PE") else "",
            _number(row[columns["close"]]), _number(row[columns["settle"]]), _number(row[columns["volume"]]), _number(row[columns["oi"]]), _number(row[columns["chg_oi"]]),
        ))
    return out


def reduce(contracts: Iterable[Contract], day: date, strikes: bool = False) -> dict[str, list[tuple]]:
    """What is kept for the day. `futures`: (symbol, instrument, expiry, close, settle, volume, oi, chg_oi) for the nearest `FUTURES_EXPIRIES` expiries not yet past.
    `totals`: (symbol, instrument, expiry, option, oi, volume) for the nearest `OPTION_EXPIRIES`. `strikes`: (symbol, expiry, strike, option, oi, close) for the nearest expiry, only
    when `strikes` is true and the contract has open interest."""
    futures: dict[tuple[str, str], list[Contract]] = defaultdict(list)
    options: dict[tuple[str, str], list[Contract]] = defaultdict(list)
    for c in contracts:
        if c.expiry < day:
            continue
        (futures if c.instrument.startswith("FUT") else options)[(c.symbol, c.instrument)].append(c)
    out: dict[str, list[tuple]] = {"futures": [], "totals": [], "strikes": []}
    for (symbol, instrument), rows in futures.items():
        for expiry in sorted({c.expiry for c in rows})[:FUTURES_EXPIRIES]:
            c = next(r for r in rows if r.expiry == expiry)
            out["futures"].append((symbol, instrument, expiry, c.close, c.settle, c.volume, c.oi, c.chg_oi))
    for (symbol, instrument), rows in options.items():
        expiries = sorted({c.expiry for c in rows})[:OPTION_EXPIRIES]
        for expiry in expiries:
            for kind in ("CE", "PE"):
                chosen = [c for c in rows if c.expiry == expiry and c.option == kind]
                if chosen:
                    out["totals"].append((symbol, instrument, expiry, kind, sum(c.oi for c in chosen), sum(c.volume for c in chosen)))
        if strikes and expiries:
            out["strikes"] += [(symbol, expiries[0], c.strike, c.option, c.oi, c.close) for c in rows if c.expiry == expiries[0] and c.option and c.oi > 0]
    return out
