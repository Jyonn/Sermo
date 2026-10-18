# 五域委托权限契约（.11 历史交付记录）

版本说明：以下记录 `.11` 交付时的历史范围和状态，不代表当前开关或客户端配置。聊天/通讯扩展及导师组合授权现已按用户明确确认上线，当前状态以 [生产精确契约](oauth-chat-contacts-contract.md) 为准；不能将本文窄版聊天范围当作当前完整群管理实现。

状态日期：2026-10-18。本文描述当前实现的动作边界，不代表任何生产客户端已获准使用。

## 状态与命名

- 五个域为 `chat`、`square`、`submission`、`contacts`、`personal`，动作是 `read`（读）、`write`（新建/发送）、`modify`（修改已有状态）。
- 15 个目录位置中，13 个已有独立适配器；`square.modify` 和 `personal.write` 没有对应动作，始终不可注册、申请或执行。目录不是 15 个通用 CRUD 权限。
- `OAUTH_DELEGATED_ACTIONS_ENABLED` 默认关闭。关闭时 13 项全部不可注册、申请、换取或使用；已签发的新权限 token 也即时失效。旧 `profile/email/phone/vip` 的已同意范围保持原样。
- `chat.events`、`chat.send` 是未发布的旧本地候选，已移除。增量事件归 `chat.read`，发送归 `chat.write`；不自动转换旧字符串。
- 代码已完成评审分支交付，并获用户明确允许发布；后端已加载实现，前端发布版本为 `.11`。新权限总开关仍关闭，注册应用范围未扩大，未产生用户授权。代码发布不代表客户端能力已开通。

## 真实动作对应表

下表“源 API”是已存在的普通会话 API，仅用于说明业务规则来源。第三方不能拿 OAuth token 调用它们；只能调用新的受限适配入口。所有已适配项均为**代码支持、开关关闭、生产能力未开通**。

| 域 / 动作 | scope | 对应源 API | 本次实际开放的动作及约束 |
| --- | --- | --- | --- |
| 聊天 / 读 | `chat.read` | `GET /chats/`、`GET /messages/`、消息事件模型 | 当前空间已加入的普通会话、成员 id/昵称、本人可见的文字历史及增量变更；遵守加入时间、隐藏/撤回、当前成员资格。不返回媒体链接、回复/转发包或投稿内容 |
| 聊天 / 写 | `chat.write` | `POST /messages/` | 向本人已加入的普通会话发送文字，必须有 `client_message_id`；保留禁言、群发言限制、内容检查和幂等。不创建群、不冒充其他账号 |
| 聊天 / 改 | `chat.modify` | `POST /chats/preference` | 仅本人会话 `pinned` 布尔字段。提醒、通知、群名、背景、成员、群主、禁言、消息删除/撤回均不包含 |
| 广场 / 读 | `square.read` | `GET /square/statements`、`GET /square/statements/{id}/comments` | 按现有空间、好友和可见性规则读取文字；匿名作者不返回 id/昵称；不返回定位、媒体、引用、签名 URL |
| 广场 / 写 | `square.write` | 同上两个路径的 `POST` | 发表文字动态（public/friends）和文字评论；保留发布权限、频率配额、广场禁言和内容检查。无匿名发布、媒体、定位、置顶或 @ 提及 |
| 广场 / 改 | `square.modify` | 当前动态/评论 detail 只有 GET/DELETE，无正文修改接口 | **没有对应动作，不开放**。不把删除、点赞取消、管理员置顶/禁言伪装成“编辑正文” |
| 投稿 / 读 | `submission.read` | `GET /chats/?purpose=submission`、`GET /messages/` | 当前作者/审核者角色能见的标题、状态和文字；遵守草稿及未释放轮次隔离、当前成员资格 |
| 投稿 / 写 | `submission.write` | `POST /chats/submissions/start`、`POST /messages/`、`POST /chats/submissions/submit` | 给当前空间管理员/协管新建文字草稿、在允许发言的阶段发文字、由作者提交；保留草稿/message 幂等和状态机。**新草稿沿用现有模型建立与收件人的好友关系**，同意文案明确说明此副作用 |
| 投稿 / 改 | `submission.modify` | `POST /chats/submissions/status` | 当前审核者在 review 阶段执行 `revision` 或 `ready`；不包含 terminate、withdraw、delete、publish 或增加审核者 |
| 通讯 / 读 | `contacts.read` | `GET /friends/`、`GET /friends/requests`、普通群列表 | 本人的好友、本人参与的申请历史和已加入的群名/id；不是通知中心，也不是设备通讯录。不返回邮箱/手机号 |
| 通讯 / 写 | `contacts.write` | `POST /friends/requests` | 向同一空间已有账号发起好友申请；不可设置来源 QR 或绕过权限。现有协管自动接受规则保留，并在同意页说明 |
| 通讯 / 改 | `contacts.modify` | `POST /friends/requests/respond` | 仅接受/拒绝本人收到的 pending 申请；发起人、旁观者、其他空间均不能操作。不删除好友、不创建永久邀请 |
| 个人 / 读 | `personal.read` | `GET /users/me` 的白名单字段 | 本人的 id、space_id、昵称、语言及五项外观设置。不直接复用 `json_me()`，不返回密码、绑定、安全、通知设置等 |
| 个人 / 写 | `personal.write` | 没有与该动作对应的“新建个人资料”接口 | **没有对应动作，不开放**。不借此创建账号或第三方应用/插件 |
| 个人 / 改 | `personal.modify` | `POST /users/me/name`、`/users/me/language`、`/users/me/personalization` | 昵称（保留设置密码/小程序身份要求、原昵称权限及冷却）、语言、`chat_bubble_style/avatar_frame_style/statement_card_style/show_self_avatar/profile_card_theme`；保留已解锁外观和 VIP/等级政策。不包含上传、自定义头像/背景或任意 URL |

旧 `profile`（昵称/头像）、`email`、`phone`、`vip` 仍作为“个人”组中独立的只读身份权限。`personal.read` 不隐含获取 email/phone/vip。双认证是所有委托动作的实时准入条件，不意味着必须索取 `phone` scope。

## 第三方适配入口

所有路径以前缀 `/users/oauth/` 开始，需要原 OAuth opaque Bearer 和匹配该 token 的 `X-OAuth-Client-ID`。普通 JWT 无效；OAuth Bearer 不转换为普通 JWT。

| 方法 / 相对路径 | scope | 请求字段 |
| --- | --- | --- |
| GET `chat/chats` | chat.read | 无 |
| GET `chat/chats/{chat_id}/messages` | chat.read | before、limit（最多100） |
| GET `chat/chats/{chat_id}/events` | chat.read | after、limit（最多100）；HTTP 增量游标，不是 WebSocket/通知订阅 |
| POST `chat/chats/{chat_id}/messages` | chat.write | content、client_message_id |
| POST `chat/chats/{chat_id}/preference` | chat.modify | pinned（严格布尔值） |
| GET `square/statements` | square.read | scope=all/friends/mine（默认friends）、before、limit |
| POST `square/statements` | square.write | text、visibility=public/friends |
| GET `square/statements/{id}/comments` | square.read | after、limit |
| POST `square/statements/{id}/comments` | square.write | text |
| GET `contacts` | contacts.read | 无 |
| POST `contacts` | contacts.write | peer_user_id（当前空间整数账号 id） |
| POST `contacts/requests/{request_id}/respond` | contacts.modify | accept（严格布尔值） |
| GET `submissions` | submission.read | 无 |
| POST `submissions` | submission.write | peer_user_id、title、client_draft_id、content、client_message_id |
| GET `submissions/{chat_id}/messages` | submission.read | after、limit |
| POST `submissions/{chat_id}/messages` | submission.write | content、client_message_id |
| POST `submissions/{chat_id}/submit` | submission.write | 空 JSON 对象 |
| POST `submissions/{chat_id}/review` | submission.modify | action=revision/ready |
| GET `personal` | personal.read | 无 |
| POST `personal` | personal.modify | 表中白名单字段的非空子集；system_language 只能和 language_preference 一起提交 |

所有写入口拒绝未知字段；读/写/改互不隐含。写入回包仅返回该动作的有限结果，不因此开放完整读取能力。动态/评论没有新设幂等键，调用方不得对结果不明的请求自动重试。普通动作依照原业务规则产生站内通知，不授予第三方读取通知或修改通知/提醒设置的能力。

## 板凳基础聊天的最小集

保留原登录契约，请求 **`profile email chat.read chat.write`**。新增仅 `chat.read` 与 `chat.write`；不需要 phone、chat.modify、投稿、通讯或个人修改。

1. BFF 创建固定整数 `space_id`、`purpose=join` 的请求，保留原7参数的精确回调、state、PKCE S256。
2. 浏览器只打开冻结 `authorization_url`，仅含 request_id；用户选取该空间账号并亲自同意。
3. 服务端换取和保存 opaque token；客户端 id 必须与 token 一致。管理员 proof 的 scope 仍为原 profile/email，不混入任何委托动作。
4. `chat.read` 用于会话列表、文字历史和每个会话的游标增量同步；`chat.write` 用于带幂等键的文字发送。媒体发送、创建会话/群、群管理或改名不在本最小集。
5. 用户资格、客户端启用状态/最新批准范围、token 到期/撤销、空间和成员资格都实时重查。原有只同意 profile/email 的 token 不会获得聊天权限，必须重新经过本人同意的新请求。

## 分组元数据与管理

- 管理列表附带 `scope_catalog`：scope、domain、action、status、available。相同 `available_scopes()` 同时决定注册、申请、token 和执行许可。
- 授权 metadata 附带 `permission_groups`，仅含实际申请的 scope。前端只描述已识别、全部列出且分组正确的请求；漏项、重复项、未知 scope 或缺少新权限元数据时拒绝继续。
- 没有这些元数据的旧后端只可展示原身份权限，不能由前端硬编码新增可用能力。

## 发布、具体确认项与回退

本分支没有数据模型变更，不需要数据库迁移、种子重跑或数据复制。

上线代码本身和开通客户端能力是两个独立步骤。生产开通前仍需具体确认：

1. 是否接受上述固定动作边界，尤其投稿新草稿的既有关系副作用和协管好友自动接受规则。
2. 代码发布已获明确允许：合入/发布默认关闭的后端适配器与配套前端（四站同步），验证部署版本和默认关闭状态；不需要重复索取代码发布许可。
3. 明确指定 `supervision-web` 的新批准 revision **仅追加 chat.read/chat.write**，保留实际回调、profile/email 和 admin-proof capability，不追加其他域或 phone。
4. 明确允许启用 `OAUTH_DELEGATED_ACTIONS_ENABLED=1`；只更新获批准客户端的 revision，经过原并发版本和审计流程。总开关不授予任何客户端权限。
5. 板凳开启配套功能前先验证完整本地契约。真实用户仍需自行发起新的同意，不代替用户授权，不升级旧 grant。

回退：首先关闭新总开关（所有新动作 token 即时被拒绝），随后按审计版本撤回该客户端新增 scope。可回退代码；无需删表、删数据、清空 token 或撤销原 profile/email 授权。不能靠回退前端来撤销后端能力。

共享模型修复：投稿提交给空间管理员时跳过给管理员本人的私聊通知，避免因“不能自己和自己创建私聊”使提交事务失败；其余状态流转与消息释放规则保持共享模型执行。

## 验证证据

- `manage.py check --settings=Sermo.isolated_test_settings` 通过。
- 91 项后端测试通过：OAuth 原协议、请求、注册表、资格、头像、新聊天/五域适配和普通投稿回归。新适配测试覆盖角色、跨域、跨空间、撤销/到期、实时双认证、关闭开关、旧 grant 不升级、成员退出、加入时间、匿名身份、禁言、未知字段、投稿未释放轮次以及个人字段事务回退。
- 15 项前端测试通过：权限分组完整性、双语描述、不可用项不可新增、未描述权限拒绝同意、授权账号选择和原请求保存；`npm run build` 通过。
- 以上是隔离 SQLite 内存数据库和虚构 fixture 测试，未读取部署设置中的凭据。当前没有真实浏览器端到端验收或第三方 BFF 联调，不能将这些测试描述成生产联调成功。
