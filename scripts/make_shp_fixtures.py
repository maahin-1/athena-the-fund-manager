"""Trim real shareholding-pattern filings to the facts the parser reads, for use as test fixtures.

    python scripts/make_shp_fixtures.py SOURCE_DIR NAME=FILE.xml [NAME=FILE.xml ...]

Writes tests/fixtures/shp/NAME.xml: the root element (with its namespaces) and the facts of the holder-group contexts the parser reads, plus the
report date, symbol and ISIN. The filings are public regulatory documents."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from athena.fundamentals.shareholding import CATEGORIES, HOLDERS, SHARES  # noqa: E402

ROOT = re.compile(r"<xbrli:xbrl[^>]*>", re.S)
FACT = re.compile(r"<([\w-]+):(\w+)\b([^>]*?\bcontextRef=\"([\w-]+)\"[^>]*)>([^<]*)</\1:\2>")
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "shp"
MAIN_TAGS = {"DateOfReport", "Symbol", "ISIN"}


def trim(text: str) -> str:
    wanted = {f"{name}_contexti".lower() for names in CATEGORIES.values() for name in names} | {f"{name}i".lower() for names in CATEGORIES.values() for name in names}
    facts = [
        m.group(0)
        for m in FACT.finditer(text)
        if (m.group(2) in MAIN_TAGS) or (m.group(4).lower() in wanted and m.group(2) in (SHARES, HOLDERS))
    ]
    head = ROOT.search(text).group(0)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + head + "\n" + "\n".join(facts) + "\n</xbrli:xbrl>\n"


def main(argv: list[str]) -> int:
    source = Path(argv[0])
    OUT.mkdir(parents=True, exist_ok=True)
    for pair in argv[1:]:
        name, file = pair.split("=", 1)
        text = (source / file).read_text(encoding="utf-8")
        trimmed = trim(text)
        (OUT / f"{name}.xml").write_text(trimmed, encoding="utf-8")
        print(f"{name}: {len(text):,} -> {len(trimmed):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
