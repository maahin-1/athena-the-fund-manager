"""Reddit posts that name a company, through Reddit's official API, for display only.

Reddit's own rules are followed: an app registered by the owner (a "script" app at reddit.com/prefs/apps), an application-only token, a descriptive User-Agent, read-only requests
at most one a second, and nothing but a handful of public subreddits about Indian investing. Off unless `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` and `REDDIT_USER_AGENT` are set. The
posts are untrusted text from strangers: they are counted and shown, never followed as instructions, and no author name is kept. Nothing here is fed to an analyst: whether what people post
says anything about the next weeks' returns has not been tested, and until it is the page labels it display-only.

A post counts only when it names the company: the company's name (without Limited) or its symbol written in capitals as a whole word in the title or text. Reddit's own search returns
posts that merely resemble the query."""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting
from athena.news.model import clip, company_names, loose_names

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/r/{subs}/search"
SUBREDDITS = "IndiaInvestments+IndianStockMarket+DalalStreetTalks+IndiaInvestments"
MIN_GAP = 1.1  # seconds between requests: under Reddit's limit with room to spare
LIMIT = 25
SNIPPET = 220
SETTINGS = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
OFF_NOTE = (
    "Reddit is off. To turn it on, register a script app at reddit.com/prefs/apps and set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT "
    "(for example 'athena-owner-research by u/yourname') in the settings file."
)

Post = Callable[[str, Mapping[str, str], tuple[str, str], Mapping[str, str]], Mapping[str, Any]]
Get = Callable[[str, Mapping[str, str], Mapping[str, str]], Mapping[str, Any]]


@dataclass(frozen=True)
class SocialPost:
    title: str
    subreddit: str
    created: datetime
    score: int
    comments: int
    link: str
    snippet: str


def _post(url: str, data: Mapping[str, str], auth: tuple[str, str], headers: Mapping[str, str]) -> Mapping[str, Any]:
    import requests

    try:
        r = requests.post(url, data=dict(data), auth=auth, headers=dict(headers), timeout=25)
    except requests.RequestException as exc:
        raise AthenaError(f"Reddit could not be reached ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise AthenaError(f"Reddit refused the token request (HTTP {r.status_code})")
    return r.json()


def _get(url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> Mapping[str, Any]:
    import requests

    try:
        r = requests.get(url, params=dict(params), headers=dict(headers), timeout=25)
    except requests.RequestException as exc:
        raise AthenaError(f"Reddit could not be reached ({type(exc).__name__})") from exc
    if r.status_code == 429:
        raise AthenaError("Reddit asked us to slow down (HTTP 429); try again later")
    if r.status_code != 200:
        raise AthenaError(f"Reddit refused the search (HTTP {r.status_code})")
    return r.json()


def credentials(env_file: Path | str = DEFAULT_ENV_FILE, environ: Mapping[str, str] | None = None) -> tuple[str, str, str] | None:
    """The three settings, or None when any is missing."""
    got = [read_setting(name, env_file, environ).strip() for name in SETTINGS]
    return (got[0], got[1], got[2]) if all(got) else None


def names_it(text: str, symbol: str, names: tuple[str, ...]) -> bool:
    """Whether a post's text names the company: the symbol as a capitalised whole word, or one of its names."""
    if re.search(rf"(?<![A-Za-z0-9&]){re.escape(symbol.upper())}(?![A-Za-z0-9&])", text):
        return True
    low = text.lower()
    return any(len(n) >= 4 and n.lower() in low for n in loose_names(names))


class RedditSearch:
    def __init__(self, creds: tuple[str, str, str] | None, post: Post = _post, get: Get = _get, sleep: Callable[[float], None] = time.sleep, subreddits: str = SUBREDDITS):
        self._creds, self._post, self._get, self._sleep, self._subs = creds, post, get, sleep, subreddits
        self._token: str | None = None

    @property
    def enabled(self) -> bool:
        return self._creds is not None

    def _bearer(self) -> str:
        if self._token is None:
            assert self._creds is not None
            got = self._post(TOKEN_URL, {"grant_type": "client_credentials"}, (self._creds[0], self._creds[1]), {"User-Agent": self._creds[2]})
            token = got.get("access_token")
            if not isinstance(token, str) or not token:
                raise AthenaError("Reddit gave no token")
            self._token = token
        return self._token

    def search(self, symbol: str, company: str) -> list[SocialPost]:
        """The posts of the last month in the chosen subreddits that name the company, newest first. Raises `AthenaError` when Reddit is off or refuses."""
        if self._creds is None:
            raise AthenaError(OFF_NOTE)
        names = company_names(company)
        query = " OR ".join(dict.fromkeys([f'"{symbol}"', *[f'"{n}"' for n in names if len(n) >= 4]]))
        headers = {"Authorization": f"Bearer {self._bearer()}", "User-Agent": self._creds[2]}
        self._sleep(MIN_GAP)
        got = self._get(SEARCH_URL.format(subs=self._subs), {"q": query, "restrict_sr": "1", "sort": "new", "t": "month", "limit": str(LIMIT), "raw_json": "1"}, headers)
        posts: list[SocialPost] = []
        seen: set[str] = set()
        for child in (got.get("data") or {}).get("children") or []:
            d = child.get("data") or {}
            title, text, permalink = str(d.get("title") or ""), str(d.get("selftext") or ""), str(d.get("permalink") or "")
            if not title or not permalink or permalink in seen or not names_it(f"{title} {text}", symbol, names):
                continue
            seen.add(permalink)
            created = d.get("created_utc")
            posts.append(
                SocialPost(
                    clip(title), str(d.get("subreddit") or ""), datetime.fromtimestamp(float(created), tz=timezone.utc) if isinstance(created, (int, float)) else datetime.fromtimestamp(0, tz=timezone.utc),
                    int(d.get("score") or 0), int(d.get("num_comments") or 0), "https://www.reddit.com" + permalink, clip(text, SNIPPET),
                )
            )
        return sorted(posts, key=lambda p: p.created, reverse=True)
