import pytest

from athena.brokers import angel_login as a
from athena.contracts import AthenaError


class FakeConnect:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, api_key):
        self.api_key = api_key
        return self

    def generateSession(self, client, pin, code):
        self.calls.append((client, pin, code))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def getfeedToken(self):
        return "FEED"


GOOD = {"status": True, "data": {"jwtToken": "JWT", "refreshToken": "REF"}}


def test_login_makes_the_code_from_the_secret_and_returns_a_session_that_hides_its_tokens():
    connect = FakeConnect(GOOD)
    session = a.login("key", "A1", "1234", "S3CR3T", connect, lambda secret: "123456")
    assert connect.api_key == "key" and connect.calls == [("A1", "1234", "123456")]
    assert session.jwt_token == "JWT" and session.feed_token == "FEED" and session.client_code == "A1"
    assert "JWT" not in repr(session) and "FEED" not in repr(session) and "A1" in repr(session)


def test_a_refusal_or_a_failure_is_an_error_that_never_holds_the_pin_the_secret_or_a_token():
    with pytest.raises(AthenaError) as info:
        a.login("key", "A1", "1234", "S3CR3T", FakeConnect({"status": False, "message": "Invalid totp"}), lambda s: "1")
    assert "Invalid totp" in str(info.value) and "1234" not in str(info.value) and "S3CR3T" not in str(info.value)
    with pytest.raises(AthenaError) as info:
        a.login("key", "A1", "1234", "S3CR3T", FakeConnect(ConnectionError("1234 S3CR3T")), lambda s: "1")
    assert "ConnectionError" in str(info.value) and "1234" not in str(info.value) and "S3CR3T" not in str(info.value)

    def bad_secret(secret):
        raise ValueError("Non-base32 digit found")

    with pytest.raises(AthenaError) as info:
        a.login("key", "A1", "1234", "S3CR3T", FakeConnect(GOOD), bad_secret)
    assert "TOTP" in str(info.value) and "S3CR3T" not in str(info.value)


def test_main_reports_success_without_printing_a_secret_and_names_what_is_missing(tmp_path, capsys):
    path = tmp_path / "settings"
    path.write_text("ANGEL_API_KEY=key\nANGEL_CLIENT_CODE=A1\nANGEL_PIN=4321\nANGEL_TOTP_SECRET=SHH\n", encoding="utf-8")
    assert a.main(["--check", "--env-file", str(path)], FakeConnect(GOOD), lambda s: "999999") == 0
    out = capsys.readouterr().out
    assert "login works for A1" in out and "4321" not in out and "SHH" not in out and "JWT" not in out and "FEED" not in out
    path.write_text("ANGEL_API_KEY=key\n", encoding="utf-8")
    assert a.main(["--check", "--env-file", str(path)], FakeConnect(GOOD)) == 1
    assert "ANGEL_PIN" in capsys.readouterr().out
