import hashlib

import pytest

from athena.brokers import kite_login as k
from athena.contracts import AthenaError


class Reply:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


def test_the_checksum_is_the_sha256_of_key_token_and_secret_in_that_order():
    assert k.checksum("key", "tok", "sec") == hashlib.sha256(b"keytoksec").hexdigest()


def test_the_request_token_is_found_in_a_pasted_address_or_taken_as_given():
    assert k.request_token_from("http://127.0.0.1/?action=login&type=login&status=success&request_token=abc123") == "abc123"
    assert k.request_token_from("  abc123 \n") == "abc123"
    assert k.request_token_from("127.0.0.1/?request_token=zzz&status=success") == "zzz"


def test_exchange_sends_the_key_token_and_checksum_and_returns_the_access_token():
    sent = {}

    def post(url, headers, data, timeout):
        sent.update(url=url, headers=headers, data=data)
        return Reply({"status": "success", "data": {"access_token": "AT"}})

    assert k.exchange("key", "sec", "rt", post) == "AT"
    assert sent["url"] == k.SESSION_URL and sent["headers"]["X-Kite-Version"] == "3"
    assert sent["data"] == {"api_key": "key", "request_token": "rt", "checksum": k.checksum("key", "rt", "sec")}


def test_a_refusal_carries_the_brokers_message_and_never_the_secret():
    def post(url, headers, data, timeout):
        return Reply({"status": "error", "error_type": "TokenException", "message": "Token is invalid or has expired."})

    with pytest.raises(AthenaError) as info:
        k.exchange("key", "topsecret", "rt", post)
    assert "TokenException" in str(info.value) and "topsecret" not in str(info.value)


def test_saving_replaces_one_line_and_leaves_the_rest(tmp_path):
    path = tmp_path / "settings"
    path.write_text("A=1\nKITE_ACCESS_TOKEN=\n# note\nB=2\n", encoding="utf-8")
    k.save_setting("KITE_ACCESS_TOKEN", "new", path)
    assert path.read_text(encoding="utf-8") == "A=1\nKITE_ACCESS_TOKEN=new\n# note\nB=2\n"
    k.save_setting("OTHER", "x", path)
    assert path.read_text(encoding="utf-8").endswith("B=2\nOTHER=x\n")


def test_main_exchanges_saves_and_prints_no_token(tmp_path, capsys):
    path = tmp_path / "settings"
    path.write_text("KITE_API_KEY=key\nKITE_API_SECRET=hush\nKITE_ACCESS_TOKEN=\n", encoding="utf-8")
    post = lambda url, headers, data, timeout: Reply({"status": "success", "data": {"access_token": "SECRETTOKEN"}})
    assert k.main(["http://x/?request_token=rt&status=success", "--env-file", str(path)], post=post) == 0
    out = capsys.readouterr().out
    assert "SECRETTOKEN" not in out and "hush" not in out
    assert "KITE_ACCESS_TOKEN=SECRETTOKEN" in path.read_text(encoding="utf-8")


def test_check_names_the_account_and_a_missing_key_stops_early(tmp_path, capsys):
    path = tmp_path / "settings"
    path.write_text("KITE_API_KEY=key\nKITE_ACCESS_TOKEN=AT\n", encoding="utf-8")
    get = lambda url, headers, timeout: Reply({"status": "success", "data": {"user_id": "AB1234", "broker": "ZERODHA", "exchanges": ["NSE", "BSE"]}})
    assert k.main(["--check", "--env-file", str(path)], get=get) == 0
    assert "AB1234" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    assert k.main(["--url", "--env-file", str(empty)]) == 1
