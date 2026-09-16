"""CLI access to the same local worker that owns browser human control."""

import httpx


def operator_request(path, method="GET", payload=None):
    with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=30) as client:
        try:
            bootstrap = client.get("/api/bootstrap")
            bootstrap.raise_for_status()
            response = client.request(
                method,
                "/api" + path,
                json=payload,
                headers={"X-BH-Operator": bootstrap.json()["operator_token"]},
            )
            if response.is_error:
                code = response.json().get("error")
                raise ValueError(
                    code
                    if isinstance(code, str) and code.isupper()
                    else "OPERATOR_REQUEST_REJECTED"
                )
            return response.json()
        except (httpx.HTTPError, KeyError):
            raise ValueError("START_BROWSER_SERVICE_ON_PORT_8765_FOR_HUMAN_CONTROL") from None
