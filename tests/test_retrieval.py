from datetime import date

from athena.retrieval import index as r

OLD, NEW = date(2024, 5, 1), date(2026, 5, 1)
DOCS = [
    r.Passage("ar2024", "Annual report 2024", OLD, 0, "The promoter has pledged 12 percent of its holding with lenders against group borrowings."),
    r.Passage("ar2024", "Annual report 2024", OLD, 1, "Revenue grew on strong demand for cloud services and large deal wins in the year."),
    r.Passage("call2026", "Concall May 2026", NEW, 0, "Management said the pledge of promoter shares was fully released and debt reduced."),
    r.Passage("call2026", "Concall May 2026", NEW, 1, "Margins improved because of lower input costs and a better product mix."),
]


def test_a_search_never_returns_a_passage_published_after_the_day_it_is_asked_as_of():
    index = r.Index(DOCS)
    early = index.search("promoter pledge", date(2025, 1, 1))
    assert [h.passage.doc_id for h in early] == ["ar2024"]
    later = index.search("promoter pledge", date(2026, 6, 1))
    assert {h.passage.doc_id for h in later} == {"ar2024", "call2026"} and r.Index(DOCS).search("pledge", date(2020, 1, 1)) == []


def test_keyword_ranking_puts_the_passage_with_the_rare_exact_term_first():
    hits = r.Index(DOCS).search("pledged lenders", date(2026, 6, 1))
    assert hits[0].passage.chunk == 0 and hits[0].passage.doc_id == "ar2024" and hits[0].keyword_rank == 1 and hits[0].meaning_rank is None


def fake_embed(texts):
    """A meaning space of two axes: money-and-debt words and growth-and-margin words."""
    debt = {"pledged", "pledge", "debt", "lenders", "borrowings", "loan", "loans", "leverage"}
    grow = {"revenue", "margins", "growth", "demand", "costs", "grew", "improved"}
    return [[float(sum(w in debt for w in r.tokens(t))), float(sum(w in grow for w in r.tokens(t)))] for t in texts]


def test_meaning_ranking_finds_a_passage_that_shares_no_word_with_the_query_and_fusion_lifts_one_both_agree_on():
    index = r.Index(DOCS, fake_embed)
    hits = index.search("leverage and loans", date(2026, 6, 1), k=4)
    assert hits[0].passage.doc_id in ("ar2024", "call2026") and hits[0].passage.chunk == 0  # the debt passages, though 'leverage' and 'loans' are in neither
    assert all(h.keyword_rank is None for h in hits[:2]) and hits[0].meaning_rank is not None
    both = index.search("promoter pledge debt", date(2026, 6, 1), k=4)[0]
    assert both.keyword_rank is not None and both.meaning_rank is not None


def test_a_quote_must_be_in_the_passage_it_cites_whitespace_and_case_aside():
    hits = r.Index(DOCS).search("promoter pledge", date(2026, 6, 1))
    ok = {"call2026#0": "the pledge of  promoter shares was FULLY released"}
    assert r.verify_quotes(ok, hits) == []
    bad = {"call2026#0": "the pledge was released and profits doubled", "ar2024#1": "revenue grew", "nowhere#9": "x", "ar2024#0": ""}
    assert r.verify_quotes(bad, hits) == ["call2026#0", "ar2024#1", "nowhere#9", "ar2024#0"]  # a paraphrase, a passage not retrieved, an unknown one, an empty quote


def test_text_is_cut_into_overlapping_chunks_that_keep_the_words_as_written():
    text = " ".join(f"w{i}" for i in range(400))
    pieces = r.chunk(text, words=180, overlap=30)
    assert pieces[0].split()[:2] == ["w0", "w1"] and pieces[1].split()[0] == "w150" and pieces[-1].split()[-1] == "w399"
    assert r.chunk("") == [] and r.chunk("short text") == ["short text"]
    made = r.passages("d", "T", OLD, text)
    assert [p.chunk for p in made] == list(range(len(pieces))) and made[0].published == OLD


def test_an_empty_index_or_a_query_of_stop_words_returns_nothing():
    assert r.Index([]).search("anything", date(2026, 1, 1)) == []
    assert r.Index(DOCS).search("the of and", date(2026, 6, 1)) == []
