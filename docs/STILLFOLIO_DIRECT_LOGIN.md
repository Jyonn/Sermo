# 从 Sermo「更多应用」直达留幅登录

## 当前结论与实施边界

现有授权码 + PKCE S256、换码、userinfo 和留幅自身会话均可复用，无需新增授权码类型或免验证登录接口。

留幅已实现用户点击“使用 Sermo 登录”后的流程，但 `src/entry.js` 目前仅识别分享页和 Sermo 回调页，没有“访问即自动开始登录”的路由。Sermo 此次已完成新的发起地址与账号选择提示。留幅完成下文入口后，两侧才能端到端联调。

目标体验：

1. 用户在 Sermo 当前空间的「更多应用 → 留幅」介绍框点击“前往”。
2. 同一新标签页打开留幅自动登录入口，显示简短连接状态并立即生成 PKCE 和 state。
3. 留幅自动跳转 Sermo 授权页，优先恢复发起时的空间账号。
4. 用户在 Sermo 确认授权，原标签页回到留幅 callback，完成换码、userinfo 和留幅会话建立。
5. 留幅进入作品集工作台。用户不必在留幅重复点击“登录 → 使用 Sermo 登录”。Sermo 授权页的资料授权确认仍保留。

## 已落地的 Sermo 侧协议

浏览器入口：

```text
https://stillfolio.jyonn.space/auth/sermo/start?source=sermo&login_hint=wzry%3A54
```

| 参数 | 用途 |
| --- | --- |
| `source=sermo` | 可选来源标记，仅用于界面，不授予权限。 |
| `login_hint=wzry:54` | 可选账号选择提示，格式为 `空间slug:用户ID`。 |

slug 只接受小写字母、数字、下划线和短横线，首位须为字母或数字，最多 64 字符；用户 ID 是正的安全整数。没有合法账号上下文时，Sermo 仍打开该入口，但不附带 `login_hint`。

Sermo「更多应用」从当前空间路径和当前已认证用户生成 hint。链接不携带 Sermo access/refresh token、手机号、昵称或 VIP 信息。

Sermo `/oauth/authorize` 已支持可选 `login_hint`：

- 若当前空间路径中的会话匹配该账号，直接展示该账号的授权确认。
- 若其他空间已有匹配的本地账号，刷新该会话并自动切换到对应空间授权页，保留完整 OAuth 参数。
- 若提示的账号不在本机，展示账号选择或登录入口，不自动授权另一账号。
- 用户主动选择其他账号后，hint 更新为所选账号；最终以同意授权的已认证账号为准。
- hint 不参与后端鉴权，也不改变 code、state、PKCE 和回调地址校验。

## 留幅需要完成的工作

### 1. 自动登录入口与静态部署

在 `stillfolio-frontend/src/entry.js` 增加 `/auth/sermo/start` 和可选尾斜杠的精确路由，加载一个轻量的自动登录模块。入口加载后自动开始登录，不先进入 `app.js` 的普通登录或引导页面。

从 query 读取并严格校验 `login_hint`；其他 query 参数不参与授权。忽略不合法 hint 后可继续走普通 Sermo 登录，不从 hint 读取或推断任何身份资料。

当前仓库的 `scripts/build-sermo-callback.mjs` 已为 callback 生成实体目录页。用同样方式生成 `dist/auth/sermo/start/index.html`，保证从 Sermo 直接导航和刷新时都能获取页面，而不是依赖返回 404 的 SPA fallback。继续保证 callback 实体页可用。

### 2. 复用现有 startSermoLogin

扩展 `src/sermo-login.js` 的 `startSermoLogin`，支持可选 `loginHint`，自动入口调用：

```js
await startSermoLogin({ returnTo: '/', returnEditor: true, loginHint });
```

保留现有流程：

- 在留幅 origin 的 sessionStorage 中生成、保存 state/verifier。
- 继续向留幅后端 `POST /api/v1/auth/sermo/start`，使用原请求字段和浏览器绑定 cookie。
- 先使用现有 `validateAuthorizeUrl(result.authorize_url, body)` 严格验证后端授权地址。
- 验证成功后，仅给该已验证的 Sermo 授权 URL 添加已校验的 `login_hint`，再跳转。

可以采用下面的顺序，保留现有严格 URL 验证函数：

```js
const destination = new URL(validateAuthorizeUrl(result.authorize_url, body));
if (loginHint) destination.searchParams.set('login_hint', loginHint);
location.assign(destination.href);
```

当前 `validateAuthorizeUrl` 拒绝未知 query 参数，因此不要在其输入中提前增加 `login_hint`。也不要为了兼容 hint 移除 origin、callback、state 或 challenge 校验。

正常按钮登录调用该函数时不传 hint，保持原有行为。新入口必须在留幅页面生成 PKCE；不能直接把 Sermo 菜单打开的 URL 当作授权回调，也不能在 Sermo origin 保存留幅 verifier。

### 3. 回调、重试与已有会话

继续复用 `src/sermo-callback.js`、`consumeSermoCallback` 和后端 `/api/v1/auth/sermo/complete`。此轮无需新增后端端点、client secret 或 Sermo refresh token。

如果重试要保留发起账号，将经过校验的 hint 一起存入 pending 事务，消费回调时带回重试函数。每次重试都生成新的 state/verifier，不能复用已消费或过期的事务。

入口需有连接中状态、失败信息及“重试 / 返回留幅”操作，不能因一个失败就自动循环跳转。callback 继续及时清理地址栏中的 code/state。

留幅若已登录其他账号，不能仅凭已有留幅会话跳过这次 Sermo 登录并直接进入原账号。保留原会话直到本次授权成功，再按经过验证的 `userinfo.sub` 建立对应会话；不要仅凭 `login_hint` 绑定或切换账户。现有同手机号冲突保护、冻结/删除检查、无手机号账号支持和本地作品数据处理继续复用。

## 联调验收

- Sermo 当前空间已登录：点击“前往”后留幅自动发起登录，Sermo 授权页展示该空间账号，无留幅额外登录按钮操作。
- 本机多空间、多账号：hint 精确选择发起账号；同一空间内其他账号不能被静默替换用于授权。
- hint 账号不可用：提供账号选择或登录路径；只有用户明确选择其他账号后才改用该账号。
- 留幅已登录其他账号、未登录或首次访问：均进入预期流程，已有数据和会话不会在失败时被清空。
- 两个标签页分别发起授权：各自 sessionStorage/state 独立，保持现有浏览器 cookie 绑定行为。
- 新入口直接访问、刷新、尾斜杠均正常；callback 可直接导航。
- 错误 hint、网络错误、取消、重复回调和 state/PKCE 错误不会登录；重试不出现循环跳转。
- 普通手机号登录、原“使用 Sermo 登录”按钮和公开分享页不回归。

## 可直接交给留幅 agent 的任务

请先读取隔壁 Sermo 仓库的 `sermo-backend/docs/STILLFOLIO_OAUTH.md` 和本文，并检查当前 `entry.js`、`sermo-login.js`、`sermo-pkce.js`、`sermo-callback.js`、`scripts/build-sermo-callback.mjs`。实现 `/auth/sermo/start` 自动登录入口、可选 login_hint 传递与重试、静态实体页面和必要测试；复用既有 PKCE、回调和后端会话流程，不修改 Sermo 仓库。按上面的验收项进行构建和联调，说明部署文件与测试结果。Sermo 侧的新入口地址和 login_hint 支持已实现，留幅需部署该入口后才能启用完整直达体验。
