"""The two vendor forwarders — APNs (HTTP/2 + ES256 provider token) and FCM (v1 + OAuth).

Both compose the notification from a STATIC string plus the two ids, and from nothing
else — there is nothing else in the process to compose from (see ``payload.py``). The
visible text is deliberately generic: any text worth varying would have to be derived
from the item, i.e. content, and the gateway never sent it.

Credentials come from the environment and are read per process, never per request —
the relay holds vendor credentials (its own), never a user's. A platform whose
credentials are absent reports itself unconfigured rather than failing mid-forward.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

logger = logging.getLogger("relay")

#: The one visible string a ping produces. Static on purpose — composed from nothing.
ALERT_TITLE = "PersonalClaw"
ALERT_BODY = "Attention needed"

_APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

#: APNs provider JWTs are valid for an hour; refresh at 50 minutes.
_APNS_JWT_LIFETIME = 50 * 60
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


@dataclass
class SendResult:
    ok: bool
    status: int
    detail: str = ""


class ApnsSender:
    """Forwards one ping as an APNs alert with the ids in the custom data."""

    def __init__(self) -> None:
        self._team_id = os.environ.get("APNS_TEAM_ID", "")
        self._key_id = os.environ.get("APNS_KEY_ID", "")
        self._private_key = os.environ.get("APNS_PRIVATE_KEY", "")
        self._topic = os.environ.get("APNS_TOPIC", "")
        env = os.environ.get("APNS_ENV", "production")
        self._host = _APNS_HOSTS.get(env, _APNS_HOSTS["production"])
        self._jwt = ""
        self._jwt_minted_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._team_id and self._key_id and self._private_key and self._topic)

    def _provider_token(self) -> str:
        if self._jwt and time.time() - self._jwt_minted_at < _APNS_JWT_LIFETIME:
            return self._jwt
        self._jwt = jwt.encode(
            {"iss": self._team_id, "iat": int(time.time())},
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_id},
        )
        self._jwt_minted_at = time.time()
        return self._jwt

    async def send(self, token: str, payload: dict[str, str]) -> SendResult:
        body = {
            "aps": {
                "alert": {"title": ALERT_TITLE, "body": ALERT_BODY},
                "sound": "default",
            },
            "kind": payload["kind"],
            "item_id": payload["item_id"],
        }
        try:
            async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
                resp = await client.post(
                    f"{self._host}/3/device/{token}",
                    json=body,
                    headers={
                        "authorization": f"bearer {self._provider_token()}",
                        "apns-topic": self._topic,
                        "apns-push-type": "alert",
                        "apns-priority": "10",
                    },
                )
            return SendResult(ok=resp.status_code == 200, status=resp.status_code)
        except Exception as exc:  # noqa: BLE001 — one forward must never kill the server
            return SendResult(ok=False, status=0, detail=type(exc).__name__)


class FcmSender:
    """Forwards one ping as an FCM v1 message with the ids in the data block."""

    def __init__(self) -> None:
        raw = os.environ.get("FCM_SERVICE_ACCOUNT_JSON", "")
        self._account: dict[str, Any] = {}
        if raw:
            try:
                self._account = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("FCM_SERVICE_ACCOUNT_JSON is not valid JSON — FCM unconfigured")
        self._project_id = str(self._account.get("project_id", ""))
        self._access_token = ""
        self._token_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._account.get("client_email") and self._project_id)

    async def _oauth_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._account["client_email"],
                "scope": _FCM_SCOPE,
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600,
            },
            self._account["private_key"],
            algorithm="RS256",
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = str(data["access_token"])
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    async def send(self, token: str, payload: dict[str, str]) -> SendResult:
        body = {
            "message": {
                "token": token,
                "notification": {"title": ALERT_TITLE, "body": ALERT_BODY},
                "data": {"kind": payload["kind"], "item_id": payload["item_id"]},
            }
        }
        try:
            access = await self._oauth_token()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send",
                    json=body,
                    headers={"authorization": f"Bearer {access}"},
                )
            return SendResult(ok=resp.status_code == 200, status=resp.status_code)
        except Exception as exc:  # noqa: BLE001 — one forward must never kill the server
            return SendResult(ok=False, status=0, detail=type(exc).__name__)
