SECRET = "nvapi-SECRET-VALUE"


class FakeResponse:
    def __init__(self, status=200, content="hello", usage=None, text="", body=None):
        self.status_code = status
        self._body = body if body is not None else {"choices": [{"message": {"content": content}}]}
        if usage is not None and body is None:
            self._body["usage"] = usage
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item
