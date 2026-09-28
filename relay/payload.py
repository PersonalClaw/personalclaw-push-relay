"""The ids-only contract — the reason this relay can exist at all.

PersonalClaw's soul guardrail forbids a cloud middle tier holding state or credentials.
The only permissible hosted component is a dumb relay carrying content-free wake-up
pings, so this module makes "content-free" STRUCTURAL: the relay refuses any request
whose payload is not exactly two short string ids. A relay that merely promised not to
log content would still receive it; this one cannot receive it.

Deliberately a standalone mirror of ``personalclaw.push.assert_content_free`` rather
than an import: anyone must be able to deploy this repo with no PersonalClaw checkout,
and the gateway's copy is the sender-side gate while this is the receiver-side one —
two gates, one vocabulary, so neither end can be talked out of the promise alone.
"""

from __future__ import annotations

from typing import Any

#: The closed platform vocabulary. The relay's whole job is picking APNs vs FCM.
PLATFORMS: tuple[str, ...] = ("ios", "android")

#: Exactly the two payload keys the gateway's ``content_free_payload`` mints.
PAYLOAD_KEYS: frozenset[str] = frozenset({"kind", "item_id"})

#: Ids are short by construction; a bound keeps a hostile body from smuggling prose
#: through an "id" field.
MAX_ID_LEN = 128

#: Device tokens: APNs is 64 hex chars, FCM registration tokens run ~150-200. The bound
#: is generous headroom, not a format check.
MAX_TOKEN_LEN = 4096


class PingError(ValueError):
    """A request that is not a content-free ping. The message is safe to return."""


def validate_ping(body: Any) -> tuple[str, str, dict[str, str]]:
    """Return ``(platform, token, payload)`` or raise :class:`PingError`.

    The payload check is an exact-shape check, not a subset check: an extra key is
    refused even if it looks harmless, because "harmless extra keys" is the exact
    slope this contract exists to stay off.
    """
    if not isinstance(body, dict):
        raise PingError("the request body must be a JSON object")

    platform = body.get("platform")
    if platform not in PLATFORMS:
        raise PingError(f"platform must be one of {list(PLATFORMS)}")

    token = body.get("token")
    if not isinstance(token, str) or not token.strip():
        raise PingError("token must be a non-empty string")
    if len(token) > MAX_TOKEN_LEN:
        raise PingError("token is implausibly long")

    payload = body.get("payload")
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise PingError("payload must be exactly {kind, item_id}")
    kind, item_id = payload["kind"], payload["item_id"]
    if not isinstance(kind, str) or not isinstance(item_id, str):
        raise PingError("payload ids must be strings")
    if not kind:
        raise PingError("payload.kind must be non-empty")
    if len(kind) > MAX_ID_LEN or len(item_id) > MAX_ID_LEN:
        raise PingError("payload ids are implausibly long")

    if set(body) != {"platform", "token", "payload"}:
        raise PingError("the request carries keys beyond {platform, token, payload}")

    return platform, token.strip(), {"kind": kind, "item_id": item_id}
