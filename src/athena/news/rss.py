from __future__ import annotations

import html
import re
import defusedxml.ElementTree as ET  # feeds are untrusted text: no entity expansion
from datetime import timezone
from email.utils import parsedate_to_datetime

from athena.contracts import AthenaError
from athena.news.model import NewsItem, clip

_TAGS = re.compile(r"<[^>]+>")


def parse_rss(body: bytes, publisher: str, source: str, publisher_from_source: bool = False) -> list[NewsItem]:
    """The items of an RSS 2.0 feed as `NewsItem`s, newest first. An item with no readable date is skipped (it cannot be placed
    in time). `publisher_from_source` is for Google News, which names the real publisher in a `source` tag and appends it
    to the title; its description repeats the title, so it carries no snippet."""
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError) as exc:
        raise AthenaError(f"not a readable feed: {exc}") from exc
    items = []
    for node in root.iter("item"):
        stamp = node.findtext("pubDate")
        try:
            published = parsedate_to_datetime(stamp.strip()) if stamp else None
        except (TypeError, ValueError):
            published = None
        title = html.unescape(node.findtext("title") or "").strip()
        if published is None or not title:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        name = publisher
        snippet = ""
        if publisher_from_source:
            name = (node.findtext("source") or publisher).strip()
            if title.endswith(f" - {name}"):
                title = title[: -len(f" - {name}")]
        else:
            snippet = clip(html.unescape(_TAGS.sub(" ", html.unescape(node.findtext("description") or ""))))
        items.append(NewsItem(clip(title), name, published, (node.findtext("link") or "").strip(), source, snippet))
    return sorted(items, key=lambda item: item.published, reverse=True)
