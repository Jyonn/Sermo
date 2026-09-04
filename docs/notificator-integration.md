# Notificator Integration

This project can send notification deliveries through `notificator-sdk`.

## 1. Config Model Keys

Notificator config is loaded from `Config` model (`Config.models.Config`).

Required keys:

- `NOTIFICATOR_NAME`
- `NOTIFICATOR_TOKEN`

Optional keys:

- `NOTIFICATOR_SDK_PATH` (default: `~/Projects/Apps/Notificator/notificator-sdk`)
- `NOTIFICATOR_HOST`
- `NOTIFICATOR_TIMEOUT` (default: `15`)

Example setup:

```python
from Config.models import Config, CI

Config.update_value(CI.NOTIFICATOR_NAME, "your_account_name")
Config.update_value(CI.NOTIFICATOR_TOKEN, "your_account_token")
Config.update_value(CI.NOTIFICATOR_HOST, "https://notice.6-79.cn")
Config.update_value(CI.NOTIFICATOR_TIMEOUT, "15")
```

## 2. Runtime Behavior

When a `NotificationEvent` is created:

1. One `NotificationDelivery` row is created per event and concrete delivery route. The database constraint makes enqueueing idempotent.
2. Disabled/unavailable channels are marked `SKIPPED`.
3. Web Push and instant-notification routes are sent immediately. Email is always handed to the scheduled worker so request threads cannot create bursts.
4. Email message deliveries for the same user are batched into one mail when multiple pending direct/group messages are ready. At most one email per user is selected in each worker pass.
5. Bark message notifications include `url=https://sermo.jyonn.space/<space>/app/chats/<chat_id>` when `open_chat_on_tap` is enabled.
6. If the offline threshold or per-user email cooldown is not reached, delivery stays `PENDING`.
7. A delivery is atomically claimed as `PROCESSING` before the external API call. Concurrent workers cannot send the same row.
8. Failed or outcome-unknown attempts are not retried automatically because Notificator does not accept an idempotency key. This favors avoiding duplicate mail over retrying an ambiguous request.
9. The scheduled command shares one dispatch budget between message digests and other pending deliveries, limiting backlog bursts after downtime.

Pending deliveries are processed later with `NotificationDelivery.process_pending()`.

Verification-code endpoints enforce their resend cooldown in the database transaction, rather than relying only on the client countdown. Capacity and identity-review emails also claim their state change atomically before starting delivery.
