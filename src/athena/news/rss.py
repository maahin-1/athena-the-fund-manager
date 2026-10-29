from __future__ import annotations

import html
import re
import defusedxml.ElementTree as ET  # feeds are untrusted text: no entity expansion
from datetime import datetime, timezone, tzinfo
from email.utils import parsedate_to_datetime

from athena.contracts import AthenaError
from athena.news.model import NewsItem, clip

_TAGS = re.compile(r"<[^>]+>")
_BARE_AMPERSAND = re.compile(rb"&(?!(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#x[0-9A-Fa-f]+);)")
_ILLEGAL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_root(body: bytes):  # noqa: ANN202
    """The feed's XML. Some publishers print an `&` in a headline without escaping it, which a strict parser refuses; on that failure one repair is tried (bare `&` escaped, control characters dropped)
    and the feed is read again. The text stays untrusted either way: it is shown and counted, never followed."""
    try:
        return ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return ET.fromstring(_ILLEGAL.sub(b"", _BARE_AMPERSAND.sub(b"&amp;", body)))


_OTHER_FORMATS = ("%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S", "%d %b, %Y %z", "%d %b, %Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S")


def parse_when(text: str | None, default_tz: tzinfo = timezone.utc) -> datetime | None:
    """A feed's date in any of the styles the Indian feeds use, timezone-aware, or None when it is not a readable date.

    RFC 822 (`Thu, 29 Oct 2026 12:05:00 +0530`), ISO 8601 (`2026-10-29T12:47:30+05:30`), and two plainer ones (`29 Oct 2026 12:00:02 +0530`, `28 Oct, 2026 +0530`). A date with no zone is read in
    `default_tz`: a feed that prints India time without saying so (the RBI's does) would otherwise be placed five and a half hours early."""
    if not text or not text.strip():
        return None
    text = text.strip()
    when: datetime | None = None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    if when is None:
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    if when is None:
        for fmt in _OTHER_FORMATS:
            try:
                when = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if when is None:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=default_tz)


def parse_rss(body: bytes, publisher: str, source: str, publisher_from_source: bool = False, default_tz: tzinfo = timezone.utc) -> list[NewsItem]:
    """The items of an RSS 2.0 feed as `NewsItem`s, newest first. An item with no readable date is skipped (it cannot be placed
    in time). `publisher_from_source` is for Google News, which names the real publisher in a `source` tag and appends it
    to the title; its description repeats the title, so it carries no snippet."""
    try:
        root = _xml_root(body)
    except (ET.ParseError, ValueError) as exc:
        raise AthenaError(f"not a readable feed: {exc}") from exc
    items = []
    for node in root.iter("item"):
        published = parse_when(node.findtext("pubDate"), default_tz)
        title = html.unescape(node.findtext("title") or "").strip()
        if published is None or not title:
            continue
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
