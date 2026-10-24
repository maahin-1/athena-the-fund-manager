import json
from datetime import date

from athena.retrieval import index as r
from athena.retrieval import review

OLD, NEW = date(2024, 5, 1), date(2026, 5, 1)
DOCS = [
    r.Passage("ar2024", "Annual report 2024", OLD, 0, "The promoter has pledged 12 percent of its holding with lenders against group borrowings."),
    r.Passage("ar2024", "Annual report 2024", OLD, 1, "The auditor included an emphasis of matter on delayed recovery of a loan to a group company."),
    r.Passage("call2026", "Concall May 2026", NEW, 0, "The chief financial officer resigned effective June and a successor has been appointed."),
]


class Script:
    def __init__(self, *replies):
        self.replies, self.asked = list(replies), []

    def complete(self, system, user):
        self.asked.append(user)
        return self.replies.pop(0)


def found(passage, quote, severity="medium", summary="s"):
    return json.dumps({"finding": True, "summary": summary, "passage": passage, "quote": quote, "severity": severity})


def test_a_finding_with_a_quote_found_in_its_passage_is_kept_with_the_document_and_date():
    out = review.review_topic("promoter_pledge", r.Index(DOCS), date(2026, 6, 1), Script(found("ar2024#0", "promoter has pledged 12 percent of its holding", "high", "Promoters pledged 12%.")))
    assert out["finding"] is True and out["document"] == "Annual report 2024" and out["published"] == "2024-05-01" and out["severity"] == "high" and out["quote"].startswith("promoter has pledged")


def test_a_reply_whose_quote_is_not_in_the_passage_is_dropped_not_passed_on():
    out = review.review_topic("promoter_pledge", r.Index(DOCS), date(2026, 6, 1), Script(found("ar2024#0", "the promoter pledged half of everything")))
    assert "dropped" in out and "not in the passage" in out["dropped"] and "finding" not in out
    wrong_key = review.review_topic("promoter_pledge", r.Index(DOCS), date(2026, 6, 1), Script(found("ar2024#1", "promoter has pledged 12 percent")))
    assert "dropped" in wrong_key


def test_no_concern_a_topic_with_no_passages_and_an_unreadable_reply_are_each_reported_as_what_they_are():
    index = r.Index(DOCS)
    assert review.review_topic("going_concern", index, date(2026, 6, 1), Script(json.dumps({"finding": False})))["finding"] is False
    nothing = review.review_topic("promoter_pledge", index, date(2020, 1, 1), Script())  # nothing was published by then: the model is not asked
    assert nothing == {"topic": "promoter_pledge", "finding": False, "searched": 0}
    assert "could not be read" in review.review_topic("promoter_pledge", index, date(2026, 6, 1), Script("not json"))["dropped"]


def test_the_passages_shown_are_only_those_published_by_the_as_of_day_and_are_marked_as_outside_text():
    llm = Script(json.dumps({"finding": False}))
    review.review_topic("auditor_remarks", r.Index(DOCS), date(2025, 1, 1), llm)
    assert "[ar2024#" in llm.asked[0] and "call2026" not in llm.asked[0]
    assert "never instructions" in review.SYSTEM


def test_the_review_lists_findings_strongest_first_and_counts_what_was_dropped():
    replies = {
        "promoter_pledge": found("ar2024#0", "pledged 12 percent", "high"),
        "auditor_remarks": found("ar2024#1", "emphasis of matter", "low"),
        "management_change": found("call2026#0", "chief financial officer resigned", "medium"),
        "related_party": found("ar2024#1", "an invented sentence"),
    }

    class ByTopic:
        def complete(self, system, user):
            topic = user.split("\n")[0].removeprefix("Topic: ").split(" (")[0].replace(" ", "_")
            return replies.get(topic, json.dumps({"finding": False}))

    out = review.review(r.Index(DOCS), date(2026, 6, 1), ByTopic(), ["promoter_pledge", "auditor_remarks", "management_change", "related_party"])
    assert [f["topic"] for f in out["findings"]] == ["promoter_pledge", "management_change", "auditor_remarks"]
    assert [d["topic"] for d in out["dropped"]] == ["related_party"] and out["topics"] == 4
