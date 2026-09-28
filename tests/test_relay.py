"""The relay's contract, falsifiable: shape refusal, honest 503s, and the log audit.

The log audit is the check that matters most: what the relay logs is ids, never
content, because a ping carries ids only. It captures every log record the relay
emits while handling real requests and asserts the token and any content-shaped
string never appear. The forwarders are faked at the sender seam; their vendor HTTP
is not this suite's subject.
"""

from __future__ import annotations

import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer

from relay.app import create_app
from relay.senders import SendResult

SECRET_TOKEN = "device-token-8f3a-SECRET"


class FakeSender:
    def __init__(self, configured: bool = True, ok: bool = True) -> None:
        self.configured = configured
        self._ok = ok
        self.sent: list[tuple[str, dict[str, str]]] = []

    async def send(self, token: str, payload: dict[str, str]) -> SendResult:
        self.sent.append((token, payload))
        return SendResult(ok=self._ok, status=200 if self._ok else 410)


@pytest.fixture()
async def client():
    app = create_app()
    app["senders"] = {"ios": FakeSender(), "android": FakeSender()}
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c
    await c.close()


def _ping(**overrides):
    body = {
        "platform": "ios",
        "token": SECRET_TOKEN,
        "payload": {"kind": "approval", "item_id": "apr-1"},
    }
    body.update(overrides)
    return body


async def test_a_valid_ping_is_forwarded_with_exactly_the_two_ids(client) -> None:
    resp = await client.post("/ping", json=_ping())
    assert resp.status == 200
    assert (await resp.json())["ok"] is True
    sender = client.app["senders"]["ios"]
    assert sender.sent == [(SECRET_TOKEN, {"kind": "approval", "item_id": "apr-1"})]


@pytest.mark.parametrize(
    "bad",
    [
        _ping(platform="windows"),
        _ping(token=""),
        _ping(payload={"kind": "approval"}),
        _ping(payload={"kind": "approval", "item_id": "a", "title": "Deploy prod?"}),
        _ping(extra="smuggled prose"),
        {"payload": {"kind": "a", "item_id": "b"}},
    ],
)
async def test_anything_that_is_not_a_content_free_ping_is_refused(client, bad) -> None:
    resp = await client.post("/ping", json=bad)
    assert resp.status == 400
    for sender in client.app["senders"].values():
        assert sender.sent == []


async def test_the_refusal_never_echoes_the_body(client) -> None:
    """An echo would put whatever the client sent into logs and proxies downstream."""
    resp = await client.post(
        "/ping", json=_ping(payload={"kind": "a", "item_id": "b", "secret": "hunter2"})
    )
    assert resp.status == 400
    assert "hunter2" not in await resp.text()


async def test_an_unconfigured_platform_is_a_named_503(client) -> None:
    client.app["senders"]["android"] = FakeSender(configured=False)
    resp = await client.post("/ping", json=_ping(platform="android"))
    assert resp.status == 503
    assert "not configured" in (await resp.json())["error"]


async def test_a_vendor_failure_is_a_502_not_a_lie(client) -> None:
    client.app["senders"]["ios"] = FakeSender(ok=False)
    resp = await client.post("/ping", json=_ping())
    assert resp.status == 502
    assert (await resp.json())["ok"] is False


async def test_healthz_names_the_routable_platforms(client) -> None:
    client.app["senders"]["android"] = FakeSender(configured=False)
    data = await (await client.get("/healthz")).json()
    assert data == {"ok": True, "platforms": ["ios"]}


async def test_the_audit_fixture_relay_logs_contain_no_content(client, caplog) -> None:
    """The log audit: drive real requests through and inspect every record emitted.

    The token must never appear (it is a capability against the vendor push service),
    and the only request-derived strings allowed are the platform and the two ids.
    """
    with caplog.at_level(logging.DEBUG):
        await client.post("/ping", json=_ping())
        await client.post("/ping", json=_ping(platform="android", token=SECRET_TOKEN + "-2"))
        # A refused request must not log its body either.
        await client.post(
            "/ping", json=_ping(payload={"kind": "a", "item_id": "b", "body": "the content"})
        )

    text = "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)
    assert "forwarded" in text, "the happy path must audit"
    assert SECRET_TOKEN not in text
    assert "the content" not in text
    for record in caplog.records:
        msg = record.getMessage()
        if "forwarded" in msg:
            assert "kind=approval" in msg and "item_id=apr-1" in msg or "platform=android" in msg
