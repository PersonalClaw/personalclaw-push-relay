# personalclaw-push-relay

A **stateless, content-free** push relay for the [PersonalClaw](https://github.com/PersonalClaw/PersonalClaw)
mobile companion. It does one thing: take an ids-only wake-up ping from *your* gateway
and forward it to APNs (iOS) or FCM (Android) so the store app can ring.

```
your gateway ──POST /ping {platform, token, payload:{kind, item_id}}──▶ relay ──▶ APNs / FCM
```

## Why this exists, and why it is this small

PersonalClaw's rule for hosted components: **no cloud middle tier holding state or
credentials**. Self-hosted push (ntfy / UnifiedPush) is the first-class, documented
default and needs no relay at all. Native APNs/FCM push, however, can only be sent by
whoever holds the app's signing credentials — so relay users route through an instance
of this service. To stay inside the rule, the relay is:

- **Stateless.** No storage, no queue, no retry, no aggregation. A request is
  forwarded and forgotten. A lost ping costs one phone wake-up; the item is still on
  your own gateway.
- **Content-free by construction, not by promise.** The only payload shape the relay
  accepts is `{kind, item_id}` — two short string ids. Anything else, including one
  extra "harmless" key, is refused with a 400 that never echoes the body
  (`relay/payload.py`). The visible notification text is a static string
  (`"PersonalClaw — Attention needed"`), because any text worth varying would have to
  be composed from the item, i.e. content the relay must never see.
- **Auditable.** One log line per forward: platform, kind, item_id, vendor status.
  Never the device token, never anything else. `tests/test_relay.py` pins this with a
  log-capture audit fixture, so the promise is a regression test.

Deploy it yourself with your own APNs/FCM credentials — the hosted instance is a
convenience, never a dependency.

## Run

```bash
pip install .
push-relay            # serves on :8080 (PORT to override)
```

Or with Docker:

```bash
docker build -t push-relay . && docker run -p 8080:8080 --env-file relay.env push-relay
```

Point the gateway at it: **Settings → Companion apps → Push backend → relay**, relay
URL `https://<your-relay>/ping` (https is required; the gateway refuses to publish to
a plaintext relay).

## Configuration (environment)

| Variable | Platform | What |
|---|---|---|
| `APNS_TEAM_ID` | ios | Apple developer team id |
| `APNS_KEY_ID` | ios | APNs auth key id |
| `APNS_PRIVATE_KEY` | ios | The `.p8` key, PEM contents |
| `APNS_TOPIC` | ios | The app bundle id |
| `APNS_ENV` | ios | `production` (default) or `sandbox` |
| `FCM_SERVICE_ACCOUNT_JSON` | android | Firebase service-account JSON, inline |
| `PORT` | — | Listen port, default 8080 |

A platform with missing credentials answers `503 not configured` — named, never
silent. `GET /healthz` reports which platforms the deployment can route to.

## API

- `POST /ping` → `{"ok": true, "status": 200}` on a delivered forward; `400` for
  anything that is not a content-free ping; `503` unconfigured platform; `502` vendor
  refusal.
- `GET /healthz` → `{"ok": true, "platforms": ["ios", "android"]}`

## Test

```bash
pip install -e '.[dev]' && pytest
```

## License

MIT — see [LICENSE](LICENSE).
