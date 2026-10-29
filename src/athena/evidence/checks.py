"""Comparing a claim with our own data. A program does this, never a model.

A claim ends as one of:
    confirmed       our data agrees (within the rounding a headline uses)
    partly          the direction agrees and the figure does not
    contradicted    our data disagrees: the claim stays visible, marked, and counts against its source
    unverified      our data covers the time and holds nothing that agrees (a rumour, or a promoter deal with no filing)
    not_checkable   our data does not reach (the insider feed stops in May; flows start the day collection did)
    pending         the data is not in yet (the day's bar, the results filing); asked again on the next pass, and given up after `PENDING_DAYS`

`confirmed`, `partly` and `contradicted` are the outcomes that build a source's record. The others say nothing about the source. A filing is the exchange's own record, so when a press item and a
filing differ the filing is what is shown as "ours", with its date; a later filing replaces an earlier one only by being a newer row, and both stay."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol

from athena.evidence.claims import IST, Claim

PENDING_DAYS = 21
RECORD_OUTCOMES = ("confirmed", "partly", "contradicted")
CRORE = 1e7
MARKET_OPEN = time(9, 15)
PROMOTER_CATEGORIES = ("Promoters", "Promoter Group")


@dataclass(frozen=True)
class Check:
    outcome: str
    detail: str
    ours: dict[str, Any] = field(default_factory=dict)
    final: bool = True


class Data(Protocol):
    def bars(self, symbol: str, since: date, until: date) -> list[tuple[date, float, float, float, float]]: ...  # (day, open, high, low, close), oldest first
    def last_bar_day(self, symbol: str) -> date | None: ...
    def quarters(self, symbol: str) -> list[dict[str, Any]]: ...  # basis, start, end, net_profit, sales, filed
    def insider(self, symbol: str, since: date, until: date) -> list[dict[str, Any]]: ...  # rows as EvidenceStore.insider_between
    def insider_newest(self) -> datetime | None: ...
    def flows(self, since: date, until: date) -> list[dict[str, Any]]: ...  # rows as EvidenceStore.flows_between
    def flows_first(self) -> date | None: ...
    def board(self, symbol: str, since: date, until: date) -> list[dict[str, Any]]: ...  # rows as EvidenceStore.board_for


def _pending(detail: str, ours: dict[str, Any] | None = None) -> Check:
    return Check("pending", detail, ours or {}, final=False)


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


# --- price ---
PRE_OPEN_FROM = time(6, 0)


def _session_move(symbol_bars: dict[date, tuple[date, float, float, float, float]], session: date) -> dict[str, float] | None:
    before = [d for d in symbol_bars if d < session]
    if not before:
        return None
    prev_close = symbol_bars[max(before)][4]
    if prev_close <= 0:
        return None
    _, _o, hi, lo, cl = symbol_bars[session]
    return {"session_date": session, "high_pct": round((hi / prev_close - 1) * 100, 2), "low_pct": round((lo / prev_close - 1) * 100, 2), "close_pct": round((cl / prev_close - 1) * 100, 2), "prev_close": prev_close}


def check_price(claim: Claim, published: datetime, data: Data) -> Check:
    """The session the headline is about. An item after the open is about that day. An item before the open is about the session before it (an evening wrap) or, from 06:00, about the day that is
    about to start (a pre-open item on a broker's downgrade), so both are tried and the claim stands if either shows it. Compared with the previous close, on the day's furthest point (a headline that
    says `jumps 5%` usually means it touched 5%) and the close, both shown."""
    local = published.astimezone(IST)
    d0 = local.date()
    last = data.last_bar_day(claim.symbol)
    if last is None:
        return Check("not_checkable", f"no price history for {claim.symbol} in the warehouse")
    by_day = {b[0]: b for b in data.bars(claim.symbol, d0 - timedelta(days=12), d0 + timedelta(days=1))}
    weekday = d0.weekday() < 5
    today_ok = weekday and local.time() >= PRE_OPEN_FROM
    sessions: list[date] = []
    waiting = False
    if today_ok and d0 in by_day:
        sessions.append(d0)
    elif today_ok and d0 > last:
        waiting = True
    if not today_ok or local.time() < MARKET_OPEN or (today_ok and d0 not in by_day and not waiting):
        earlier = [d for d in by_day if d < d0]
        if earlier:
            sessions.append(max(earlier))
    if not sessions and waiting:
        return _pending(f"the price bar for {d0} is not loaded yet (the warehouse has prices to {last})")
    moves = [m for m in (_session_move(by_day, s) for s in sessions) if m]
    if not moves:
        return Check("not_checkable", f"no session with a previous close found before {d0}")
    want, direction = float(claim.fields["pct"]), int(claim.fields["direction"])
    tolerance = max(0.5, 0.1 * want)
    word = "rose" if direction > 0 else "fell"
    shown = []
    for m in moves:
        m["extreme_pct"] = m["high_pct"] if direction > 0 else -m["low_pct"]
        shown.append(m)
    shown.sort(key=lambda m: m["extreme_pct"], reverse=True)
    best = shown[0]
    ours = {"session": best["session_date"].isoformat(), "high_pct": best["high_pct"], "low_pct": best["low_pct"], "close_pct": best["close_pct"], "prev_close": best["prev_close"]}
    if max(abs(best["high_pct"]), abs(best["low_pct"])) > 30:
        return Check("not_checkable", f"{claim.symbol} moved over 30% from the previous close on {best['session_date']}: a split, bonus or other adjustment may sit in the bar", ours)
    if best["extreme_pct"] >= want - tolerance:
        return Check("confirmed", f"on {best['session_date']} it {word} as much as {abs(best['extreme_pct']):.1f}% from the previous close and closed {best['close_pct']:+.1f}%", ours)
    if waiting:
        return _pending(f"the earlier session does not show it and the price bar for {d0} is not loaded yet (the warehouse has prices to {last})", ours)
    tried = " and ".join(f"{m['session_date']} (high {m['high_pct']:+.1f}%, low {m['low_pct']:+.1f}%, close {m['close_pct']:+.1f}%)" for m in shown)
    return Check("contradicted", f"the furthest it {word} from the previous close was {max(best['extreme_pct'], 0):.1f}% on {tried}", ours)


# --- results ---
def _quarter_value(row: dict[str, Any], metric: str) -> float | None:
    value = row.get(metric)
    return float(value) if isinstance(value, (int, float)) else None


def check_results(claim: Claim, published: datetime, now: datetime, data: Data) -> Check:
    quarters = data.quarters(claim.symbol)
    metric, direction, want, amount, basis = claim.fields["metric"], int(claim.fields["direction"]), float(claim.fields["pct"]), claim.fields.get("amount_cr"), claim.fields["basis"]
    pub = published.replace(tzinfo=None) if published.tzinfo is None else published.astimezone(timezone.utc).replace(tzinfo=None)
    near = [q for q in quarters if q.get("filed") and pub - timedelta(days=7) <= q["filed"] <= pub + timedelta(days=1)]
    if not near:
        newest = max((q["end"] for q in quarters), default=None)
        held = f"the newest quarter held ends {newest}" if newest else "no quarterly figures are held for it"
        if now.astimezone(timezone.utc).replace(tzinfo=None) - pub < timedelta(days=10):
            return _pending(f"no results filing within a week of the item yet; {held}")
        return Check("unverified", f"no results filing within a week of the item; {held}")
    latest_end = max(q["end"] for q in near)
    by_basis: dict[str, dict[date, dict[str, Any]]] = {}
    for q in quarters:
        by_basis.setdefault(q["basis"], {})[q["end"]] = q
    tried = []
    best: Check | None = None
    for name in ("consolidated", "standalone"):
        series = by_basis.get(name, {})
        cur = series.get(latest_end)
        if cur is None or _quarter_value(cur, metric) is None:
            continue
        gap = timedelta(days=365) if basis == "yoy" else timedelta(days=91)
        prior = min((q for e, q in series.items() if abs((latest_end - e) - gap) <= timedelta(days=12)), key=lambda q: abs((latest_end - q["end"]) - gap), default=None)
        now_v = _quarter_value(cur, metric)
        before_v = _quarter_value(prior, metric) if prior else None
        if now_v is None or before_v is None or before_v <= 0:
            tried.append(f"{name}: no comparable {'year-ago' if basis == 'yoy' else 'previous'} quarter")
            continue
        actual = (now_v / before_v - 1) * 100
        ours = {"basis": name, "quarter_end": latest_end.isoformat(), "value_cr": round(now_v / CRORE, 1), "prior_cr": round(before_v / CRORE, 1), "pct": round(actual, 1), "filed": cur["filed"].isoformat()}
        direction_ok = _sign(actual) == direction
        pct_ok = abs(abs(actual) - want) <= max(1.0, 0.05 * want)
        amount_ok = None if amount is None else abs(now_v / CRORE - amount) <= max(1.0, 0.02 * amount)
        said = f"{claim.fields['metric'].replace('_', ' ')} {'up' if direction > 0 else 'down'} {want:g}%" + (f" to Rs {amount:g} crore" if amount is not None else "")
        text = f"{name} {latest_end}: {now_v / CRORE:,.0f} crore against {before_v / CRORE:,.0f} a {'year' if basis == 'yoy' else 'quarter'} earlier, {actual:+.1f}% (filed {cur['filed']:%Y-%m-%d})"
        if direction_ok and pct_ok and amount_ok is not False:
            return Check("confirmed", text, ours)
        outcome = "partly" if direction_ok else "contradicted"
        candidate = Check(outcome, f"headline said {said}; {text}", ours)
        if best is None or (best.outcome == "contradicted" and outcome == "partly"):
            best = candidate
    if best is not None:
        return best
    return Check("not_checkable", "; ".join(tried) or f"the {metric.replace('_', ' ')} line is not in the filed figures for quarter {latest_end}")


# --- insiders ---
def check_insider(claim: Claim, published: datetime, now: datetime, data: Data) -> Check:
    action = claim.fields["action"]
    pub_day = published.astimezone(IST).date()
    promoter = "promoter" in claim.text.lower()
    rows = data.insider(claim.symbol, pub_day - timedelta(days=10), pub_day + timedelta(days=6))
    rows = [r for r in rows if (not promoter or r["category"] in PROMOTER_CATEGORIES)
            and ((r["trade_from"] and pub_day - timedelta(days=10) <= r["trade_from"] <= pub_day + timedelta(days=2)) or pub_day - timedelta(days=2) <= r["filed_at"].date() <= pub_day + timedelta(days=6))]
    agree = [r for r in rows if r["txn"] == action]
    if agree:
        shares = sum(r["shares"] or 0 for r in agree)
        first = agree[0]
        return Check("confirmed", f"{len(agree)} {action.lower()} filing{'s' if len(agree) > 1 else ''} by {'promoters' if promoter else 'insiders'}, about {shares:,.0f} shares, filed {first['filed_at']:%Y-%m-%d}",
                     {"filings": len(agree), "shares": shares, "filed": first["filed_at"].isoformat(), "link": first.get("link") or ""})
    other = [r for r in rows if r["txn"] in ("Buy", "Sell", "Pledge", "Pledge Revoke", "Pledge Invoke")]
    if other:
        kinds = sorted({r["txn"] for r in other})
        return Check("contradicted", f"the exchange's insider filings in that window show {', '.join(kinds)}, not {action}", {"filed_as": kinds, "filings": len(other)})
    newest = data.insider_newest()
    reach = newest.date().isoformat() if newest else "nothing held"
    if newest is None or newest.astimezone(IST).date() < pub_day + timedelta(days=6):
        if now.astimezone(timezone.utc).replace(tzinfo=None) - published.astimezone(timezone.utc).replace(tzinfo=None) < timedelta(days=14):
            return _pending(f"insider filings held reach {reach}, not yet past the item")
        return Check("not_checkable", f"the insider filings held end {reach}, before the item could be checked")
    return Check("unverified", "no promoter filing of that kind within the days around the item")


# --- board meetings ---
def check_board(claim: Claim, published: datetime, now: datetime, data: Data) -> Check:
    when = date.fromisoformat(claim.fields["date"])
    rows = data.board(claim.symbol, when - timedelta(days=60), when + timedelta(days=60))
    if any(r["meeting_date"] == when for r in rows):
        hit = next(r for r in rows if r["meeting_date"] == when)
        return Check("confirmed", f"the exchange has a board-meeting notice for {when} ({hit['purpose'] or 'notice'}, announced {hit['announced_at']:%Y-%m-%d})", {"date": when.isoformat(), "announced": hit["announced_at"].isoformat()})
    if rows:
        dates = sorted({r["meeting_date"].isoformat() for r in rows})
        return Check("contradicted", f"the exchange's notices for {claim.symbol} name {', '.join(dates)}, not {when}", {"dates": dates})
    if now.astimezone(timezone.utc).replace(tzinfo=None) - published.astimezone(timezone.utc).replace(tzinfo=None) < timedelta(days=3):
        return _pending("no board-meeting notice held for it yet")
    return Check("unverified", f"no board-meeting notice for {claim.symbol} within two months of {when}")


# --- institutional flows ---
def check_flow(claim: Claim, published: datetime, now: datetime, data: Data) -> Check:
    local = published.astimezone(IST)
    day = local.date() if local.hour >= 12 else local.date() - timedelta(days=1)
    first = data.flows_first()
    cat = claim.fields["category"]
    rows = [r for r in data.flows(day - timedelta(days=4), day) if r["category"] == cat]
    if not rows:
        if first is None or day < first:
            return Check("not_checkable", f"our flow history starts {first or 'when collection starts'}, after {day}")
        if now.astimezone(timezone.utc).replace(tzinfo=None) - published.astimezone(timezone.utc).replace(tzinfo=None) < timedelta(days=2):
            return _pending(f"no {cat} flow held for {day} yet")
        return Check("unverified", f"no {cat} flow held for {day}")
    row = max(rows, key=lambda r: r["day"])
    net, want, direction = float(row["net"]), float(claim.fields["amount_cr"]), int(claim.fields["direction"])
    ours = {"day": row["day"].isoformat(), "net_cr": net, "buy_cr": row["buy"], "sell_cr": row["sell"]}
    said = f"{cat} {'bought' if direction > 0 else 'sold'} Rs {want:,.0f} crore"
    got = f"the exchange shows {cat} net {net:+,.2f} crore on {row['day']} (bought {row['buy']:,.2f}, sold {row['sell']:,.2f})"
    if _sign(net) != direction:
        return Check("contradicted", f"headline said {said}; {got}", ours)
    err = abs(abs(net) - want)
    if err <= max(25.0, 0.05 * want):
        return Check("confirmed", got, ours)
    if err <= 0.3 * want:
        return Check("partly", f"headline said {said}; {got}", ours)
    return Check("contradicted", f"headline said {said}; {got}", ours)


def check_claim(claim: Claim, published: datetime, now: datetime, data: Data) -> Check:
    """One claim, compared with the data it can be compared with. A claim still pending after `PENDING_DAYS` is closed as not checkable, with the reason it waited."""
    if claim.kind == "rumour":
        return Check("unverified", "a report of a plan or talks: nothing in a filing or a price can confirm it here")
    if claim.kind == "price_move":
        result = check_price(claim, published, data)
    elif claim.kind == "results":
        result = check_results(claim, published, now, data)
    elif claim.kind == "insider":
        result = check_insider(claim, published, now, data)
    elif claim.kind == "board_meeting":
        result = check_board(claim, published, now, data)
    elif claim.kind == "flow":
        result = check_flow(claim, published, now, data)
    else:
        return Check("not_checkable", f"no check exists for {claim.kind}")
    if not result.final and now.astimezone(timezone.utc) - published.astimezone(timezone.utc) > timedelta(days=PENDING_DAYS):
        return Check("not_checkable", f"{result.detail} (given up after {PENDING_DAYS} days)", result.ours)
    return result
