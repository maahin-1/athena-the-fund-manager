"""Trim real NSE results filings to the facts the statement mapping reads, for use as test fixtures.

    python scripts/make_xbrl_fixtures.py SOURCE_DIR NAME=FILE.xml [NAME=FILE.xml ...]

Writes tests/fixtures/xbrl/NAME.xml: the root element (with its namespaces), every context without a dimension, and the facts whose tag the mapping
reads. The filings are public regulatory documents; only the figures the tests assert on are kept."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from athena.fundamentals.lines import all_tags  # noqa: E402

ROOT = re.compile(r"<xbrli:xbrl[^>]*>", re.S)
CONTEXT = re.compile(r"<xbrli:context\b.*?</xbrli:context>", re.S)
CONTEXT_ID = re.compile(r'<xbrli:context\s+id="([\w-]+)"')
FACT = re.compile(r"<([\w-]+):(\w+)\b([^>]*?\bcontextRef=\"([\w-]+)\"[^>]*)>([^<]*)</\1:\2>")
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xbrl"


def trim(text: str) -> str:
    keep = all_tags()
    every = CONTEXT.findall(text)
    contexts = [c for c in every if "<xbrli:scenario" not in c]
    dimensioned = {CONTEXT_ID.match(c).group(1) for c in every if "<xbrli:scenario" in c}
    # some filings never define their base contexts (OneD, FourD, OneI): a fact is kept unless its context is a dimensioned one
    facts = [m.group(0) for m in FACT.finditer(text) if m.group(2) in keep and m.group(4) not in dimensioned]
    head = ROOT.search(text).group(0)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + head + "\n" + "\n".join(contexts + facts) + "\n</xbrli:xbrl>\n"


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
