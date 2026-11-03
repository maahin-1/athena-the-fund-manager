"""Cut a draft offer document (DRHP, RHP, prospectus) into its sections by code.

The document has a fixed order and prints each heading in capitals at the top of its first page ("SECTION II - RISK FACTORS", "OBJECTS OF THE OFFER", "OUR BUSINESS"); the wording differs a little
between issuers ("the Offer" or "the Issue", "Section V" or "Section VI"), so a heading is matched by what it says, not by its number. A section starts on the first page after the contents where one
of the first few lines is its heading in capitals, and runs to the page before the next section starts. Pages before the first heading are the `front` (cover, responsibility statement, contents).

The finder never guesses: a heading it does not see is reported as missing, and its pages stay with the section before it, so a person can see what was not separated."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

TOP_LINES = 6  # a heading is within the first few lines of its page (the page number and a "SECTION" line may come first)
LEADERS_IN_CONTENTS = 5  # a contents page has a dotted line to a page number for every entry
_PREFIX = re.compile(r"^SECTION\s+[IVXL]+\s*[–—-]\s*", re.IGNORECASE)
_LEADER = re.compile(r"\.{5,}")
_NOISE = re.compile(r"^[\dIVXLC .]+$")

# (key, title shown to the owner, heading pattern on a line already stripped of "SECTION X -", in the order the sections come)
SPEC: tuple[tuple[str, str, str], ...] = (
    ("contents", "Table of contents", r"TABLE OF CONTENTS$"),
    ("general", "General and definitions", r"GENERAL$|DEFINITIONS AND ABBREVIATIONS$"),
    ("conventions", "Conventions and forward-looking statements", r"CERTAIN CONVENTIONS|FORWARD[- ]LOOKING STATEMENTS$"),
    ("risk_factors", "Risk factors", r"RISK FACTORS$"),
    ("introduction", "Introduction", r"INTRODUCTION$"),
    ("offer_summary", "The offer", r"THE (OFFER|ISSUE)$"),
    ("summary_financials", "Summary of financial information", r"SUMMARY OF (RESTATED )?FINANCIAL INFORMATION$"),
    ("general_information", "General information", r"GENERAL INFORMATION$"),
    ("capital_structure", "Capital structure", r"CAPITAL STRUCTURE$"),
    ("objects", "Objects of the offer", r"OBJECTS OF THE (OFFER|ISSUE)$"),
    ("basis_for_price", "Basis for offer price", r"BASIS FOR (THE )?(OFFER|ISSUE) PRICE$"),
    ("tax_benefits", "Special tax benefits", r"STATEMENT OF (POSSIBLE )?SPECIAL TAX BENEFITS$"),
    ("about_company", "About the company", r"ABOUT OUR COMPANY$"),
    ("industry", "Industry overview", r"INDUSTRY OVERVIEW$"),
    ("business", "Our business", r"OUR BUSINESS$"),
    ("regulations", "Key regulations", r"KEY REGULATIONS AND POLICIES$"),
    ("history", "History and corporate matters", r"HISTORY AND CERTAIN CORPORATE MATTERS$"),
    ("management", "Our management", r"OUR MANAGEMENT$"),
    ("promoters", "Promoters and promoter group", r"OUR PROMOTERS AND PROMOTER GROUP$"),
    ("dividend", "Dividend policy", r"DIVIDEND POLICY$"),
    ("financials", "Restated financial information", r"FINANCIAL INFORMATION$|RESTATED FINANCIAL INFORMATION|INDEPENDENT AUDITOR.S EXAMINATION REPORT"),
    ("other_financial", "Other financial information", r"OTHER FINANCIAL INFORMATION$"),
    ("mdna", "Management's discussion and analysis", r"MANAGEMENT.S DISCUSSION AND ANALYSIS"),
    ("capitalisation", "Capitalisation statement", r"CAPITALI[SZ]ATION STATEMENT$"),
    ("indebtedness", "Financial indebtedness", r"FINANCIAL INDEBTEDNESS$"),
    ("legal", "Legal and other information", r"LEGAL AND OTHER INFORMATION$"),
    ("litigation", "Outstanding litigation", r"OUTSTANDING LITIGATION"),
    ("approvals", "Government and other approvals", r"GOVERNMENT AND OTHER APPROVALS$"),
    ("group_companies", "Group companies", r"OUR GROUP COMPANIES$"),
    ("regulatory_disclosures", "Other regulatory and statutory disclosures", r"OTHER REGULATORY AND STATUTORY DISCLOSURES$"),
    ("offer_information", "Offer information", r"(OFFER|ISSUE)[- ](RELATED )?INFORMATION$|TERMS OF THE (OFFER|ISSUE)$"),
    ("offer_structure", "Offer structure", r"(OFFER|ISSUE) STRUCTURE$"),
    ("offer_procedure", "Offer procedure", r"(OFFER|ISSUE) PROCEDURE$"),
    ("articles", "Articles of association", r"MAIN PROVISIONS OF THE ARTICLES|DESCRIPTION OF EQUITY SHARES AND TERMS OF"),
    ("other_information", "Other information", r"OTHER INFORMATION$|MATERIAL CONTRACTS AND DOCUMENTS"),
    ("declaration", "Declaration", r"DECLARATION$"),
)
_COMPILED = tuple((key, title, re.compile(pattern)) for key, title, pattern in SPEC)
TITLES = {key: title for key, title, _ in SPEC} | {"front": "Cover and front pages"}
# sections whose absence means the reading is incomplete: the owner is told when one is not found
CORE = ("risk_factors", "objects", "capital_structure", "basis_for_price", "business", "management", "promoters", "financials", "mdna", "litigation", "approvals")


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    start: int  # first page, 1-based
    end: int  # last page, inclusive

    @property
    def pages(self) -> int:
        return self.end - self.start + 1


def _top_lines(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln and not _NOISE.match(ln)][:TOP_LINES]


def _is_contents_page(text: str) -> bool:
    return sum(1 for ln in text.splitlines() if _LEADER.search(ln)) >= LEADERS_IN_CONTENTS


def _headings(text: str) -> list[str]:
    """The keys of the headings a page opens with, in the order the specification lists them."""
    if _is_contents_page(text):
        return []
    found: list[str] = []
    for line in _top_lines(text):
        if line != line.upper() or not re.search(r"[A-Z]{3}", line) or len(line) > 110:
            continue
        for candidate in (line, _PREFIX.sub("", line)):
            for key, _, pattern in _COMPILED:
                if key not in found and pattern.match(candidate):
                    found.append(key)
    return found


def find_sections(pages: Sequence[str]) -> list[Section]:
    """The sections of a document given its pages' text, in page order. A section found twice is taken at its first heading; one that is not found is simply absent."""
    first: dict[str, int] = {}
    for number, text in enumerate(pages, start=1):
        for key in _headings(text):
            first.setdefault(key, number)
    order = {key: n for n, (key, _, _) in enumerate(SPEC)}
    marks = sorted(first.items(), key=lambda kv: (kv[1], order[kv[0]]))
    out: list[Section] = []
    if marks and marks[0][1] > 1:
        out.append(Section("front", TITLES["front"], 1, marks[0][1] - 1))
    for n, (key, start) in enumerate(marks):
        end = marks[n + 1][1] - 1 if n + 1 < len(marks) else len(pages)
        if end >= start:  # a heading sharing its page with the next one (a section divider above its first chapter) holds no pages of its own
            out.append(Section(key, TITLES[key], start, end))
    return out


def section_of(sections: Sequence[Section], page: int) -> str:
    """The key of the section holding `page`, or `front` for a page before the first heading."""
    for s in sections:
        if s.start <= page <= s.end:
            return s.key
    return "front"


def missing(sections: Sequence[Section]) -> list[str]:
    """The titles of the core sections the finder did not see."""
    held = {s.key for s in sections}
    return [TITLES[k] for k in CORE if k not in held]
