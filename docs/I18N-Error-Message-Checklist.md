# Error Message i18n 落地清单

适用范围：`Sermo Backend` 的 API 错误返回（`message` / `user_message`）。

## 1. 代码层规范

1. 错误定义（`Error(...)`）使用 `gettext_lazy`。
2. 参数校验提示（`Validator(..., message=...)`）使用 `gettext_lazy`。
3. 涉及模板变量的 lazy 文案，使用 `format_lazy`，不要在 import 时直接 `.format(...)`。
4. 前端不使用 `message` 做分支判断，只使用 `identifier`。

## 2. 语言选择机制

1. 后端开启 `LocaleMiddleware`。
2. 支持的语言在 `LANGUAGES` 中声明（当前：`en`, `zh-CN`）。
3. 前端请求统一带 `Accept-Language`，示例：
   - `Accept-Language: zh-CN`
   - `Accept-Language: en`

## 3. 翻译文件流程

1. 抽取文案：
   ```bash
   django-admin makemessages -l zh_CN -l en
   ```
2. 翻译 `locale/zh_CN/LC_MESSAGES/django.po` 与 `locale/en/LC_MESSAGES/django.po`。
3. 编译：
   ```bash
   django-admin compilemessages
   ```
4. 重启服务。

## 4. 联调验收用例

1. 缺少 token：
   - 请求：不带 `Authorization`
   - 期望：`Accept-Language=zh-CN` 时返回中文错误文案，`en` 返回英文。
2. 参数错误：
   - 请求：`/messages` 传 `limit=1`
   - 期望：返回对应语言的校验文案。
3. 权限错误：
   - 请求：非好友发起 `/chats/direct`
   - 期望：返回对应语言的 `Users are not friends` 文案翻译。
4. 业务错误：
   - 请求：错误邮箱验证码
   - 期望：返回对应语言文案。

## 5. 回归检查重点

1. `message` 与 `user_message` 是否都完成翻译。
2. `identifier` 是否保持稳定不受语言影响。
3. 变量插值是否正确（如 `{min_length}`, `{password_length}`）。
4. 新增错误文案是否已进入 `.po` 文件。

## 6. 常见坑

1. 使用 `gettext` 而不是 `gettext_lazy`，导致文案在进程启动时固定语言。
2. lazy 文案直接 `.format(...)`，导致提前求值或格式异常。
3. 前端把 `message` 当业务分支键，切语言后逻辑失效。

