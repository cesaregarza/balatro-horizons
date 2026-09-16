"""Counting response for tests whose subject is the generation transport."""

import httpx


def with_input_count(receive):
    def wrapped(request):
        if request.url.path.endswith(("/input_tokens", "/count_tokens")):
            return httpx.Response(200, json={"input_tokens": 100})
        return receive(request)

    return wrapped
