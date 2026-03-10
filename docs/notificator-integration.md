# Notificator Integration

This project can send notification deliveries through `notificator-sdk`.

## 1. Config Model Keys

Notificator config is loaded from `Config` model (`User.models.Config`).

Required keys:

- `NOTIFICATOR_NAME`
- `NOTIFICATOR_TOKEN`

Optional keys:

- `NOTIFICATOR_SDK_PATH` (default: `~/Projects/Apps/Notificator/notificator-sdk`)
- `NOTIFICATOR_HOST`
- `NOTIFICATOR_TIMEOUT` (default: `15`)

Example setup:

```python
from User.models import Config, CI

Config.update_value(CI.NOTIFICATOR_NAME, "your_account_name")
Config.update_value(CI.NOTIFICATOR_TOKEN, "your_account_token")
Config.update_value(CI.NOTIFICATOR_SDK_PATH, "~/Projects/Apps/Notificator/notificator-sdk")
Config.update_value(CI.NOTIFICATOR_HOST, "https://notice.6-79.cn")
Config.update_value(CI.NOTIFICATOR_TIMEOUT, "15")
```

## 2. Runtime Behavior

When a `NotificationEvent` is created:

1. `NotificationDelivery` rows are created per channel preference.
2. Disabled/unavailable channels are marked `SKIPPED`.
3. If offline threshold is reached, the system sends immediately via SDK.
4. If threshold is not reached, delivery stays `PENDING`.

Pending deliveries can be retried later with `NotificationDelivery.process_pending()`.
