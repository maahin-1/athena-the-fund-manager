"""The registry of sources: who they are, how far to trust them to begin with, and the ones that are off and why.

Tier is a starting prior, not a measurement: **A** the exchange, a regulator or a company's own filing; **B** an established publisher or agency; **C** an aggregator, an attention measure, anything unedited.
The page says "prior" beside a tier until a source has enough checked claims to be judged on its record. A source that cannot be read honestly is listed with `off_reason` so the page can say why, rather
than disappearing. Feeds were each fetched and parsed on 2026-10-29; a feed that answers but stopped updating is listed off with the date it stopped."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TIERS = ("A", "B", "C")


@dataclass(frozen=True)
class FeedSource:
    id: str
    name: str  # the publisher as the reader knows it
    kind: str  # press | regulator
    tier: str
    url: str
    scope: str = "company"  # company: matched to a company by name; market: shown as market and policy news
    tz: object = timezone.utc  # the zone of a date the feed prints without one
    off_reason: str = ""  # non-empty: not collected, and this is why

    @property
    def active(self) -> bool:
        return not self.off_reason


FEED_SOURCES: tuple[FeedSource, ...] = (
    # the four publishers the Sentiment page already read
    FeedSource("et_stocks", "Economic Times", "press", "B", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    FeedSource("et_markets", "Economic Times", "press", "B", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    FeedSource("bs_markets", "Business Standard", "press", "B", "https://www.business-standard.com/rss/markets-106.rss"),
    FeedSource("bs_companies", "Business Standard", "press", "B", "https://www.business-standard.com/rss/companies-101.rss"),
    FeedSource("mint_markets", "Mint", "press", "B", "https://www.livemint.com/rss/markets"),
    FeedSource("mint_companies", "Mint", "press", "B", "https://www.livemint.com/rss/companies"),
    FeedSource("bl_markets", "BusinessLine", "press", "B", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    FeedSource("bl_companies", "BusinessLine", "press", "B", "https://www.thehindubusinessline.com/companies/feeder/default.rss"),
    # added 2026-10-29
    FeedSource("cnbctv18_markets", "CNBC-TV18", "press", "B", "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml"),
    FeedSource("ndtv_profit", "NDTV Profit", "press", "B", "https://feeds.feedburner.com/ndtvprofit-latest"),
    FeedSource("toi_business", "Times of India", "press", "B", "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms"),
    FeedSource("businesstoday_markets", "Business Today", "press", "B", "https://www.businesstoday.in/rssfeeds/?id=225346"),
    FeedSource("hindu_business", "The Hindu", "press", "B", "https://www.thehindu.com/business/Economy/feeder/default.rss"),
    FeedSource("indianexpress_business", "Indian Express", "press", "B", "https://indianexpress.com/section/business/feed/"),
    FeedSource("zee_companies", "Zee Business", "press", "B", "https://www.zeebiz.com/companies.xml"),
    FeedSource("zee_latest", "Zee Business", "press", "B", "https://www.zeebiz.com/latest.xml"),
    FeedSource("sebi", "SEBI", "regulator", "A", "https://www.sebi.gov.in/sebirss.xml", scope="market", tz=IST),
    FeedSource("rbi_press", "RBI", "regulator", "A", "https://www.rbi.org.in/pressreleases_rss.xml", scope="market", tz=IST),
    # listed so the page can say why they are not read
    FeedSource("moneycontrol", "Moneycontrol", "press", "B", "https://www.moneycontrol.com/rss/latestnews.xml", off_reason="its feeds answer but stopped updating on 2024-04-23"),
    FeedSource("pib", "PIB", "regulator", "A", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", scope="market", off_reason="its feed carries no dates, so an item cannot be placed in time"),
    FeedSource("financial_express", "Financial Express", "press", "B", "https://www.financialexpress.com/market/stock-market/feed/", off_reason="the feed addresses tried return a web page or malformed XML"),
    FeedSource("zee_market_news", "Zee Business", "press", "B", "https://www.zeebiz.com/market-news.xml", off_reason="this feed answers 403; the company and latest feeds are read instead"),
)

@dataclass(frozen=True)
class StructuredSource:
    """A source that is not a feed of stories but rows of data (filings, flows). `scope` company: rows name a company; market: rows are about the whole market."""

    id: str
    name: str
    kind: str  # filing | flow
    tier: str
    scope: str


STRUCTURED_SOURCES: tuple[StructuredSource, ...] = (
    StructuredSource("nse_pit", "NSE insider trades", "filing", "A", "company"),
    StructuredSource("nse_board", "NSE board meetings", "filing", "A", "company"),
    StructuredSource("nse_fiidii", "NSE FII/DII flows", "flow", "A", "market"),
)

@dataclass(frozen=True)
class Tried:
    """A source that was tried (or considered) for the attention and try-and-see phases and is not read, with the date and the reason."""

    id: str
    name: str
    tried: str
    reason: str


# E4 and E5 of the evidence plan: each tried once with one polite request and the honest user agent (robots.txt read first); none was pursued past a refusal.
TRIED_AND_OFF: tuple[Tried, ...] = (
    Tried("gdelt", "GDELT", "2026-10-29 and 2026-11-09", "answers 429 ('limit requests to one every 5 seconds') on a first request and again after an 8-second pause; the limit is shared and cannot be met from here, so it is not read"),
    Tried("bse_announcements", "BSE announcements", "2026-11-09", "its announcements address answers with its web application page, not data, to a plain request; reading it would need a browser's headers, which is not tried"),
    Tried("yahoo_news", "Yahoo Finance news", "2026-11-09", "its feed answers but carries no items for NSE tickers (0 for RELIANCE.NS; 17 for a US control); and its terms limit use to personal reading"),
    Tried("google_trends", "Google Trends", "2026-11-09", "no official interface; the library route is unofficial and rate-limited, so it is off by choice"),
    Tried("financial_express", "Financial Express feed", "2026-11-09", "the feed address answers with a web page again (200, text/html)"),
)
BY_ID = {s.id: s for s in FEED_SOURCES}
assert len(BY_ID) == len(FEED_SOURCES) and not set(BY_ID) & {s.id for s in STRUCTURED_SOURCES}, "source ids must be unique"


def tier_of(publisher: str, source: str = "") -> str:
    """The tier of an item that came in through the older news path (the exchange's announcements are A; a publisher is B; anything else C)."""
    if source == "nse":
        return "A"
    return "B" if any(s.name == publisher for s in FEED_SOURCES) else "C"
