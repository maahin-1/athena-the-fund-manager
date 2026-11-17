"""Offline review probes. No provider/router construction or credential-file access."""
import json
from datetime import date

from athena.council import debate, judge
from athena.evaluation.grounding import check_grounded
from athena.forensic.model import Case, OBSERVATION
from athena.forensic.text.lane import DocData
from athena.forensic.tools import ToolContext
from athena.forensic.verify import verify_entry
from athena.forensic.ownership.lane import pattern_quarters

packet = {"metrics": {"roe": {"value": -0.25, "unit": "fraction"}, "pe": {"value": 20, "unit": "ratio"}}}
for claim in ("Return on equity is positive 25%.", "Revenue grew 20%.", "The company is certain to outperform."):
    print("numeric_grounding", claim, vars(check_grounded(claim, packet)))

sheet = {"facts": [{"id": "F1", "value": 20}], "blend": {"rating": "Hold", "conviction": 20}}
print("debate_without_citations", debate._check(json.dumps({"case": "The company is certain to outperform.", "evidence": [], "weakest_point": "None."}), sheet))
print("judge_without_citations", judge._check(json.dumps({"stance": "Buy", "why": "The company is certain to outperform.", "relied_on": [], "would_change_my_mind": "Nothing.", "conviction": "same", "look_again_days": 20}), sheet))

lookup = lambda ev: {"result": {"text": "The company held its board meeting."}}
print("unsupported_forensic_text", verify_entry(OBSERVATION, {"text": "Revenue collapsed by 95% and all creditors refused payment.", "evidence": ["E1"]}, lookup))

class FakeDocs:
    def document(self, target):
        return {"published": date(2027, 1, 1), "n_pages": 1}

data = DocData()
data._store = FakeDocs()
ctx = ToolContext(Case("probe", "drhp", "future-doc", date(2025, 1, 1)), None, "text")
print("future_document_accepted", data.document(ctx))
print("missing_filing_date_accepted", pattern_quarters({date(2025, 3, 31): {"total": (100, None), "fii": (10, None)}}, {}, date(2025, 3, 31)))
