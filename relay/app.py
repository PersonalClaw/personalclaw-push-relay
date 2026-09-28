"""The relay: one POST, zero state.

``POST /ping`` takes ``{platform, token, payload:{kind, item_id}}``, forwards it to
APNs or FCM, and answers with the vendor outcome. Nothing is stored, queued, retried,
or aggregated — a relay that buffered pings would be a cloud middle tier holding state,
which is the exact thing PersonalClaw's soul guardrail forbids. A lost ping costs one
phone wake-up; the item itself is still on the user's own gateway.

**The log line is part of the contract.** One line per forward: platform, kind,
item_id, vendor status. Never the token (a capability against the vendor push
service), and never anything else — there is nothing else in the process (see
``payload.py``). The test suite's audit fixture asserts this against captured records,
so "relay logs contain no content" is a regression test, not a policy document.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from relay.payload import PingError, validate_ping
from relay.senders import ApnsSender, FcmSender

logger = logging.getLogger("relay")

#: Content-free pings are tiny; anything bigger than this is not one.
_MAX_BODY = 8 * 1024


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


async def ping(request: web.Request) -> web.Response:
    raw = await request.read()
    if len(raw) > _MAX_BODY:
        return _error("body too large for a content-free ping", 413)
    try:
        body: Any = json.loads(raw)
    except json.JSONDecodeError:
        return _error("the request body must be JSON", 400)

    try:
        platform, token, payload = validate_ping(body)
    except PingError as exc:
        # The message names the shape violation — a client-programming fact. The body
        # itself is never echoed: an echo would put whatever the client sent into the
        # sender's logs and proxies, defeating the refusal.
        return _error(str(exc), 400)

    sender = request.app["senders"][platform]
    if not sender.configured:
        return _error(f"the {platform} platform is not configured on this relay", 503)

    result = await sender.send(token, payload)
    logger.info(
        "forwarded platform=%s kind=%s item_id=%s status=%s ok=%s",
        platform,
        payload["kind"],
        payload["item_id"],
        result.status,
        result.ok,
    )
    return web.json_response(
        {"ok": result.ok, "status": result.status}, status=200 if result.ok else 502
    )


async def healthz(request: web.Request) -> web.Response:
    """Liveness plus which platforms this deployment can actually route to."""
    senders = request.app["senders"]
    return web.json_response(
        {"ok": True, "platforms": sorted(p for p, s in senders.items() if s.configured)}
    )


def create_app() -> web.Application:
    app = web.Application(client_max_size=_MAX_BODY)
    app["senders"] = {"ios": ApnsSender(), "android": FcmSender()}
    app.router.add_post("/ping", ping)
    app.router.add_get("/healthz", healthz)
    return app


def main() -> None:
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    web.run_app(create_app(), port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
