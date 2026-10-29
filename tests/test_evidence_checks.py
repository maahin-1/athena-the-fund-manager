from datetime import date, datetime, timedelta, timezone

from athena.evidence import checks as ck
from athena.evidence.claims import Claim

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class FakeData:
    def __init__(self, bars=None, quarters=None, insider=None, newest=None, flows=None, flows_first=None, board=None):
        self._bars, self._quarters, self._insider, self._newest = bars or [], quarters or [], insider or [], newest
        self._flows, self._flows_first, self._board = flows or [], flows_first, board or []

    def bars(self, symbol, since, until):
        return [b for b in self._bars if since <= b[0] <= until]

    def last_bar_day(self, symbol):
        return max((b[0] for b in self._bars), default=None)

    def quarters(self, symbol):
        return self._quarters

    def insider(self, symbol, since, until):
        return [r for r in self._insider if since <= r["filed_at"].date() <= until]

    def insider_newest(self):
        return self._newest

    def flows(self, since, until):
        return [r for r in self._flows if since <= r["day"] <= until]

    def flows_first(self):
        return self._flows_first

    def board(self, symbol, since, until):
        return [r for r in self._board if since <= r["meeting_date"] <= until]


def bar(day, o, h, l, c):  # noqa: E741
    return (day, o, h, l, c)


# 2026-10-27 Tuesday close 100; 2026-10-28 Wednesday high 106 low 99 close 103
BARS = [bar(date(2026, 10, 26), 99, 101, 98, 100), bar(date(2026, 10, 27), 100, 101, 99, 100), bar(date(2026, 10, 28), 100, 106, 99, 103)]
at = lambda y, m, d, h, mi=0: datetime(y, m, d, h, mi, tzinfo=timezone(timedelta(hours=5, minutes=30)))  # noqa: E731
price = lambda direction, pct: Claim("price_move", "INFY", "x", {"direction": direction, "pct": pct})  # noqa: E731


def test_a_jump_is_confirmed_when_the_day_touched_the_level_and_the_close_is_shown():
    got = ck.check_claim(price(1, 5), at(2026, 10, 28, 11), NOW, FakeData(BARS))
    assert got.outcome == "confirmed" and got.ours["high_pct"] == 6.0 and got.ours["close_pct"] == 3.0 and "closed +3.0%" in got.detail


def test_a_fall_that_never_happened_is_contradicted_with_the_real_range():
    got = ck.check_claim(price(-1, 5), at(2026, 10, 28, 11), NOW, FakeData(BARS))
    assert got.outcome == "contradicted" and "high +6.0%" in got.detail and got.final


def test_an_item_in_the_small_hours_is_about_the_session_before_it():
    got = ck.check_claim(price(1, 5), at(2026, 10, 28, 3, 30), NOW, FakeData(BARS))
    assert got.ours["session"] == "2026-10-27" and got.outcome == "contradicted"  # that day's high was only +1%


def test_a_pre_open_item_stands_if_either_the_session_before_or_the_day_about_to_start_shows_it():
    got = ck.check_claim(price(1, 5), at(2026, 10, 28, 8, 30), NOW, FakeData(BARS))
    assert got.outcome == "confirmed" and got.ours["session"] == "2026-10-28"  # a broker's downgrade at 08:30 is about that morning
    evening_wrap = ck.check_claim(price(1, 5), at(2026, 10, 29, 8, 30), NOW, FakeData(BARS))
    assert evening_wrap.outcome == "confirmed" and evening_wrap.ours["session"] == "2026-10-28"  # today's bar is not in, yesterday's shows it
    waiting = ck.check_claim(price(1, 9), at(2026, 10, 29, 8, 30), NOW, FakeData(BARS))
    assert waiting.outcome == "pending" and not waiting.final  # yesterday does not show it and today's bar is not loaded: not called wrong yet


def test_the_days_bar_not_loaded_yet_is_pending_not_wrong_and_is_given_up_after_three_weeks():
    got = ck.check_claim(price(1, 5), at(2026, 10, 29, 11), NOW, FakeData(BARS))
    assert got.outcome == "pending" and not got.final and "not loaded yet" in got.detail
    late = ck.check_claim(price(1, 5), at(2026, 10, 29, 11), datetime(2026, 11, 25, tzinfo=UTC), FakeData(BARS))
    assert late.outcome == "not_checkable" and "given up after 21 days" in late.detail


def test_a_huge_one_day_move_is_not_compared_because_it_may_be_a_split():
    split = [bar(date(2026, 10, 27), 100, 100, 100, 100), bar(date(2026, 10, 28), 20, 21, 19, 20)]
    got = ck.check_claim(price(-1, 5), at(2026, 10, 28, 11), NOW, FakeData(split))
    assert got.outcome == "not_checkable" and "split" in got.detail


def test_a_symbol_with_no_prices_is_not_checkable():
    assert ck.check_claim(price(1, 5), at(2026, 10, 28, 11), NOW, FakeData([])).outcome == "not_checkable"


# --- results ---
def q(basis, end, profit, sales, filed):
    return {"basis": basis, "start": end - timedelta(days=90), "end": end, "net_profit": profit * 1e7, "sales": sales * 1e7, "filed": filed}


QUARTERS = [
    q("consolidated", date(2025, 9, 30), 7364, 40986, datetime(2025, 10, 16, 16, 14)),
    q("consolidated", date(2026, 6, 30), 7769, 41764, datetime(2026, 7, 23, 12, 10)),
    q("consolidated", date(2026, 9, 30), 7823, 42279, datetime(2026, 10, 23, 14, 46)),
    q("standalone", date(2025, 9, 30), 6900, 38000, datetime(2025, 10, 16, 16, 14)),
    q("standalone", date(2026, 9, 30), 7000, 40000, datetime(2026, 10, 23, 14, 46)),
]
profit = lambda direction, pct, amount=None, basis="yoy": Claim("results", "INFY", "x", {"metric": "net_profit", "direction": direction, "pct": pct, "amount_cr": amount, "basis": basis})  # noqa: E731


def test_a_profit_claim_is_confirmed_against_the_filed_quarter_and_the_filing_date_is_shown():
    got = ck.check_claim(profit(1, 6, 7823), datetime(2026, 10, 23, 11, tzinfo=UTC), NOW, FakeData(quarters=QUARTERS))
    assert got.outcome == "confirmed" and got.ours["basis"] == "consolidated" and got.ours["pct"] == 6.2 and "filed 2026-10-23" in got.detail


def test_the_right_direction_with_the_wrong_figure_is_partly_and_the_wrong_direction_is_contradicted():
    near = datetime(2026, 10, 23, 11, tzinfo=UTC)
    assert ck.check_claim(profit(1, 15), near, NOW, FakeData(quarters=QUARTERS)).outcome == "partly"
    assert ck.check_claim(profit(1, 6, 9000), near, NOW, FakeData(quarters=QUARTERS)).outcome == "partly"  # the percentage fits and the rupee amount does not
    got = ck.check_claim(profit(-1, 6), near, NOW, FakeData(quarters=QUARTERS))
    assert got.outcome == "contradicted" and "headline said net profit down 6%" in got.detail


def test_results_not_yet_filed_are_pending_then_unverified_never_wrong():
    early = datetime(2026, 10, 28, 11, tzinfo=UTC)
    old_only = [x for x in QUARTERS if x["end"] != date(2026, 9, 30)]
    got = ck.check_claim(profit(1, 6), early, NOW, FakeData(quarters=old_only))
    assert got.outcome == "pending" and "newest quarter held ends 2026-06-30" in got.detail
    later = ck.check_claim(profit(1, 6), early, datetime(2026, 11, 20, tzinfo=UTC), FakeData(quarters=old_only))
    assert later.outcome in ("unverified", "not_checkable")


# --- insiders ---
def trade(txn, category="Promoters", filed=datetime(2026, 10, 27, 10, 0, tzinfo=UTC), trade_from=date(2026, 10, 26), shares=1000.0):
    return {"txn": txn, "category": category, "filed_at": filed, "trade_from": trade_from, "shares": shares, "link": "https://x/a"}


insider = lambda action, text="Promoters sell stake": Claim("insider", "INFY", text, {"action": action})  # noqa: E731
PUB = datetime(2026, 10, 28, 9, tzinfo=UTC)


def test_a_promoter_sale_is_confirmed_by_a_filing_and_contradicted_by_the_opposite_filing():
    reach = datetime(2026, 10, 28, tzinfo=UTC)
    assert ck.check_claim(insider("Sell"), PUB, NOW, FakeData(insider=[trade("Sell")], newest=reach)).outcome == "confirmed"
    got = ck.check_claim(insider("Sell"), PUB, NOW, FakeData(insider=[trade("Buy")], newest=reach))
    assert got.outcome == "contradicted" and "Buy" in got.detail
    covered = datetime(2026, 11, 20, tzinfo=UTC)  # the data reaches past the days around the item
    other = ck.check_claim(insider("Sell"), PUB, NOW, FakeData(insider=[trade("Sell", category="Director")], newest=covered))
    assert other.outcome == "unverified"  # a director's sale is not a promoter's
    assert ck.check_claim(insider("Sell"), PUB, NOW, FakeData(insider=[trade("Sell", category="Director")], newest=reach)).outcome == "pending"  # the data has not reached the end of that window yet


def test_when_the_insider_data_stops_before_the_item_the_claim_is_not_checkable_and_not_unverified():
    stale = datetime(2026, 5, 1, tzinfo=UTC)
    young = ck.check_claim(insider("Sell"), PUB, NOW, FakeData(newest=stale))
    assert young.outcome == "pending" and "2026-05-01" in young.detail
    old = ck.check_claim(insider("Sell"), PUB, datetime(2026, 11, 20, tzinfo=UTC), FakeData(newest=stale))
    assert old.outcome == "not_checkable" and not old.ours.get("filings")
    assert ck.check_claim(insider("Sell"), PUB, NOW, FakeData(newest=datetime(2026, 11, 20, tzinfo=UTC))).outcome == "unverified"


# --- board meetings ---
def meeting(day):
    return {"meeting_date": day, "purpose": "Board Meeting Intimation", "announced_at": datetime(2026, 10, 28, 12, 25, tzinfo=UTC)}


board = lambda day: Claim("board_meeting", "INFY", "x", {"date": day})  # noqa: E731


def test_a_board_meeting_date_is_confirmed_or_contradicted_by_the_exchanges_notices():
    assert ck.check_claim(board("2026-11-05"), PUB, NOW, FakeData(board=[meeting(date(2026, 11, 5))])).outcome == "confirmed"
    got = ck.check_claim(board("2026-11-05"), PUB, NOW, FakeData(board=[meeting(date(2026, 11, 12))]))
    assert got.outcome == "contradicted" and "2026-11-12" in got.detail
    assert ck.check_claim(board("2026-11-05"), PUB, NOW, FakeData()).outcome == "pending"
    assert ck.check_claim(board("2026-11-05"), PUB, datetime(2026, 11, 3, tzinfo=UTC), FakeData()).outcome == "unverified"


# --- flows ---
def flow_row(day, cat, net, buy=1000.0, sell=1000.0):
    return {"day": day, "category": cat, "net": net, "buy": buy, "sell": sell}


flow = lambda cat, direction, amt: Claim("flow", "", "x", {"category": cat, "direction": direction, "amount_cr": amt})  # noqa: E731


def test_a_flow_claim_is_compared_in_sign_and_size_with_the_exchanges_day():
    data = FakeData(flows=[flow_row(date(2026, 10, 28), "FII/FPI", -3500.5)], flows_first=date(2026, 10, 28))
    when = datetime(2026, 10, 28, 14, tzinfo=UTC)  # 19:30 India time: the day's own figures
    assert ck.check_claim(flow("FII/FPI", -1, 3500), when, NOW, data).outcome == "confirmed"
    assert ck.check_claim(flow("FII/FPI", -1, 4200), when, NOW, data).outcome == "partly"
    assert ck.check_claim(flow("FII/FPI", -1, 9000), when, NOW, data).outcome == "contradicted"
    got = ck.check_claim(flow("FII/FPI", 1, 3500), when, NOW, data)
    assert got.outcome == "contradicted" and "net -3,500.50 crore" in got.detail


def test_a_flow_before_the_history_begins_is_not_checkable_not_wrong():
    data = FakeData(flows=[flow_row(date(2026, 10, 28), "DII", 100.0)], flows_first=date(2026, 10, 28))
    got = ck.check_claim(flow("DII", 1, 100), datetime(2026, 10, 20, 14, tzinfo=UTC), NOW, data)
    assert got.outcome == "not_checkable" and "starts 2026-10-28" in got.detail


def test_a_rumour_is_unverified_and_final_and_an_unknown_kind_is_not_checkable():
    assert ck.check_claim(Claim("rumour", "INFY", "x", {}), PUB, NOW, FakeData()) == ck.Check("unverified", "a report of a plan or talks: nothing in a filing or a price can confirm it here")
    assert ck.check_claim(Claim("other", "INFY", "x", {}), PUB, NOW, FakeData()).outcome == "not_checkable"
