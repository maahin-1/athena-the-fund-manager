from datetime import date, datetime

from athena.contracts import AthenaError
from athena.fundamentals import annual_reports as ar
from athena.fundamentals.ingest import Report

REPORTS = [{"title": "FY2018-19", "url": "u", "published": datetime(2019, 3, 30)}]
REF = {date(2019, 3, 31): {"income": {"sales": 1e10}, "balance": {}}}
PAGE = "Consolidated Statement of Profit and Loss Total expenses Profit before tax " + " ".join(str(1000 + i) for i in range(40))


class Model:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def complete(self, system, user):
        if self.error:
            raise self.error
        return self.reply


def step_for(read_pages, llm):
    return ar.chain(REPORTS, REF, read_pages, llm, back_to=2019)["steps"][0]


def test_a_reply_that_is_not_json_is_the_models_failure_not_the_reports():
    step = step_for(lambda url: [PAGE], Model(reply="I think the answer is"))
    assert step["model"] is True and step["network"] is False and step["result"].startswith("unreadable")


def test_a_pdf_library_error_on_a_damaged_report_is_neither_the_models_nor_the_exchanges():
    class PdfReadError(Exception):
        pass

    def read(url):
        raise PdfReadError("Invalid object in /Pages")

    step = step_for(read, Model(reply="{}"))
    assert step["model"] is False and step["network"] is False and "PdfReadError" in step["result"]
    step = step_for(lambda url: (_ for _ in ()).throw(AthenaError("the archive is damaged")), Model(reply="{}"))
    assert step["model"] is False and step["network"] is False


def test_the_exchange_refusing_or_not_answering_while_the_report_is_fetched_is_a_network_failure():
    for text in ("HTTP 503", "no reply", "HTTP 429"):
        def read(url, text=text):
            raise AthenaError(text)

        step = step_for(read, Model(reply="{}"))
        assert step["network"] is True and step["model"] is False, text


def test_only_model_and_network_failures_count_as_failures_of_a_company_and_only_the_models_count_toward_the_model_stop(tmp_path, monkeypatch):
    from test_annual_reports import year

    from athena.fundamentals.documents import Doc
    from athena.fundamentals.store import FundamentalsStore
    from athena.fundamentals.universe import Member

    path = tmp_path / "f.duckdb"
    with FundamentalsStore(path) as store:
        store.save_company("X", "X Ltd", "INE1", "IT", "indas", 1e6)
        store.save_documents("X", ["annual_report"], [Doc("annual_report", "FY2018-19", "", datetime(2019, 3, 30), "u", "")])
        store.save_statements(ar.to_statements("X", "consolidated", {date(2019, 3, 31): year(1000)}), "https://x/1.xml", datetime(2020, 5, 1), source="nse-xbrl")

    class FakeReader:
        failure = None

        def __init__(self, results):
            pass

        def pages(self, url):
            if FakeReader.failure:
                raise FakeReader.failure
            return [PAGE]

    monkeypatch.setattr(ar, "Reader", FakeReader)
    real_cache = ar.BlocksCache
    monkeypatch.setattr(ar, "BlocksCache", lambda: real_cache(tmp_path / "blocks"))

    def run(llm):
        return ar.ingest_symbol(Member("X", "X Ltd", "IT", "INE1"), None, path, llm=llm, dry_run=True, back_to=2019, log=[])

    FakeReader.failure = AthenaError("the archive is damaged")
    damaged = run(Model(reply="{}"))
    FakeReader.failure = AthenaError("HTTP 503")
    refused = run(Model(reply="{}"))
    FakeReader.failure = None
    silent = run(Model(reply="no json here"))
    assert (damaged.failed, damaged.model_failed) == (0, 0) and damaged.notes  # a fact about the report: noted, not a failure
    assert (refused.failed, refused.model_failed) == (1, 0)
    assert (silent.failed, silent.model_failed) == (1, 1)
