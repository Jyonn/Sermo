# Sermo 前端产品与设计规范（FE Design Spec v1）

- 文档版本：`v1.0`
- 文档日期：`2026-03-13`
- 文档目标：为前端工程师提供可直接开工的产品、视觉、交互、状态与 API 对接规范。
- 约束依据：
  - 你提供的产品方向（Space 模型、年轻个性群体）
  - 当前后端代码真实能力（`Space/User/Friendship/Chat/Message`）

---

## 1. 产品定位与体验目标

### 1.1 目标用户

- 年轻、表达欲强、重视圈层归属感的用户。
- 进入门槛要低，愿意先体验聊天，再逐步完成账号完善。

### 1.2 产品一句话

`Sermo` 是“以 Space 为单位的即时社交空间”，强调“快速加入、轻社交、强个性表达”。

### 1.3 体验关键词

- 快：3 步进入聊天。
- 酷：视觉有态度，不是通用企业 IM。
- 清：信息层级清晰，不做复杂学习成本。
- 稳：消息、在线状态、好友关系的反馈必须确定。

---

## 2. 信息架构（IA）

## 2.1 一级结构

1. `进入层`：创建 Space、登录 Space、加入 Space。
2. `会话层`：聊天列表、消息面板、发送区。
3. `关系层`：好友列表、好友申请、Space 用户列表、在线用户。
4. `设置层`：账号升级（邮箱+密码）、联系方式绑定（Email/SMS/Bark）、通知偏好。

## 2.2 推荐前端路由

```txt
/entry
/space/create
/space/login
/space/join
/app
/app/chats
/app/chats/:chatId
/app/friends
/app/friends/requests
/app/space-users
/app/space-users/online
/app/settings/account
/app/settings/notifications
/app/settings/contacts
```

说明：
- `/app` 后路由均要求 `User JWT`。
- `Space token` 当前仅在 Space 注册/登录场景返回，业务主流程暂不依赖。

---

## 3. 视觉设计语言（Design Language）

## 3.1 视觉方向

主题名：`Neon Street`（霓虹街头）

- 气质：年轻、果断、有表达感。
- 基调：浅底+高饱和点缀，不走“纯商务蓝”或“紫色模板风”。
- 风格元素：贴纸感圆角、强对比按钮、轻微噪点背景、动态渐变边缘光。

## 3.2 字体系统

- 标题：`Space Grotesk`（600/700）
- 正文：`Noto Sans SC`（400/500）
- 数据与编码：`JetBrains Mono`（500）

CSS 建议：

```css
:root {
  --font-heading: "Space Grotesk", "Noto Sans SC", sans-serif;
  --font-body: "Noto Sans SC", "PingFang SC", sans-serif;
  --font-mono: "JetBrains Mono", "SFMono-Regular", monospace;
}
```

## 3.3 色彩 Token（可直接代码化）

```css
:root {
  --bg-canvas: #f5f4ef;
  --bg-surface: #ffffff;
  --bg-elevated: #fffdf8;

  --text-primary: #111318;
  --text-secondary: #4f5562;
  --text-muted: #8a92a3;

  --brand-primary: #00d084;
  --brand-primary-press: #00b874;
  --brand-secondary: #ff5a3d;
  --brand-accent: #00a6ff;

  --line-soft: #e8ebf2;
  --line-strong: #d7dce7;

  --success: #16c47f;
  --warning: #ffb020;
  --danger: #ff4d6d;
  --info: #00a6ff;

  --chat-self: #111318;
  --chat-self-text: #ffffff;
  --chat-other: #eef2fb;
  --chat-other-text: #111318;
}
```

## 3.4 间距与圆角

- 8pt 栅格：`4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48`
- 圆角：
  - 卡片 `16`
  - 输入框 `14`
  - 按钮 `12`
  - 气泡 `18`

## 3.5 阴影与层级

```css
:root {
  --shadow-sm: 0 2px 8px rgba(17, 19, 24, 0.06);
  --shadow-md: 0 8px 24px rgba(17, 19, 24, 0.10);
  --shadow-lg: 0 18px 40px rgba(17, 19, 24, 0.14);
}
```

## 3.6 动效规范

- 页面入场：`220ms`，`ease-out`，透明度+8px 位移。
- 列表项渐入：`120ms` stagger。
- 消息新到达：`160ms` 轻弹（scale 0.98 -> 1）。
- 禁止无意义动效；所有动效都必须服务于状态变化反馈。

---

## 4. 布局与响应式

## 4.1 桌面端（>= 1024）

三栏布局：

1. 左栏（280）：会话列表 + 搜索 + 新建入口。
2. 中栏（最小 420）：消息区。
3. 右栏（320）：资料/成员/群设置（按上下文切换）。

## 4.2 平板（768-1023）

- 默认双栏：会话列表 + 消息区。
- 右栏信息改为抽屉。

## 4.3 移动端（< 768）

- 单栏栈式：`会话列表 -> 对话 -> 详情`。
- 底部一级导航：`聊天 | 好友 | 空间 | 设置`。

---

## 5. 关键页面设计

## 5.1 进入页（Entry）

目标：让用户 10 秒内选择路径并进入。

模块：

- 创建 Space
- Space 邮箱验证码登录
- 加入 Space（昵称必填，密码可选）

交互要求：

- 表单错误在字段下方实时提示。
- 验证码发送后显示倒计时（建议 60s）。
- `slug` 输入自动转小写。

## 5.2 聊天主界面（App Shell）

左栏：

- 搜索（会话名/用户名）
- 会话列表项：标题、最后消息摘要、时间、未读角标、在线点
- `+` 菜单：发起私聊、创建群、查看邀请

中栏：

- 顶部：会话标题、成员数、更多操作
- 消息流：按时间连续分组（同人同分钟可并组）
- 输入区：文本输入（先支持文本），回车发送，Shift+Enter 换行

右栏：

- 私聊：对方资料、好友状态、操作
- 群聊：群名、成员列表、邀请与移除（仅群主）

## 5.3 好友模块

- 好友列表：已接受好友
- 申请列表：`incoming/outgoing` 分栏
- 操作：发起申请、同意、拒绝、删除好友关系

权限提示：

- `Basic` 用户隐藏“发起好友申请”主按钮。
- 若用户直接触发接口被拒绝，弹出升级引导。

## 5.4 Space 用户与在线用户

- 用户页：支持关键字检索 + 分页加载。
- 在线页：只看在线用户。
- 用户卡片操作：发起私聊、发起好友申请（受权限控制）。

## 5.5 设置页

账号：

- 当前身份：`Basic / Verified`
- 邮箱验证升级流程：`发送验证码 -> 输入验证码 + 密码 -> 升级成功`

通知偏好：

- 每个渠道独立配置：启用开关 + 离线阈值（分钟）
- 阈值输入支持步进器与数字输入双模式

联系方式绑定：

- Email/SMS/Bark：`发送验证码 -> 输入验证码 -> 绑定`

---

## 6. 核心交互状态机

## 6.1 认证状态机

1. `Anonymous`
2. `SpaceAuthenticated`（有 Space token，仅空间层）
3. `UserAuthenticated`（有 user access + refresh）
4. `TokenRefreshing`
5. `Expired`（刷新失败，回到 Entry）

策略：

- 业务主界面以 `UserAuthenticated` 为准。
- 请求 401 时自动刷新一次，成功后重放失败请求。

## 6.2 消息拉取状态机（无 WebSocket 版本）

1. 初次进入会话：`GET /messages?chat_id&limit=30`
2. 上滑历史：`GET /messages?chat_id&before=<min_id>&limit=30`
3. 增量轮询：`GET /messages?chat_id&after=<max_id>&limit=30`
4. 全局拉新：`GET /messages/sync?after=<global_message_id>&limit=200`

轮询建议：

- 前台激活会话：每 2-3 秒。
- 页面后台：每 15-30 秒。

## 6.3 在线状态与心跳

- 心跳 API：`GET /users/heartbeat`
- 建议频率：每 `60s`。
- UI 在线判定：以后端 `is_alive` 为准，不在前端自行推断。

---

## 7. API 对接规范（按当前后端实现）

说明：以下均为实际代码可用接口。路径前缀默认 `/`。

## 7.1 通用约定

- 鉴权头：`Authorization: Bearer <token>`
- 成功响应：取 `body` 作为业务数据。
- 失败响应：展示 `user_message`，并记录 `identifier` 供埋点。

## 7.2 Space 相关

1. `POST /spaces/email-code`
- 作用：发送 Space 邮箱验证码（注册或登录）
- JSON：
  - `slug` 可空（空=注册；有值=登录）
  - `email`
- 返回：`{ expires_in }`

2. `POST /spaces`
- 作用：创建 Space
- JSON：`name, slug, email, code`
- 返回：`{ space, auth }`（auth 为 Space token）

3. `POST /spaces/login`
- 作用：Space 邮箱验证码登录
- JSON：`slug, email, code`
- 返回：`{ space, auth }`（auth 为 Space token）

4. `POST /spaces/join`
- 作用：用户加入/登录 Space
- JSON：`slug, name, password(可空), language(必填: en/zh-CN)`
- 返回：`{ space, auth }`
  - 此处 `auth` 为用户登录信息：`{ auth, refresh, data }`

5. `GET /spaces/me`
- 作用：获取当前用户所属 Space
- 鉴权：User token

6. `GET /spaces/users`
- 作用：Space 用户列表
- Query：`q, online, limit, offset`

7. `GET /spaces/users/online`
- 作用：在线用户列表（强制在线过滤）
- Query：`q, limit, offset`

## 7.3 User 相关

1. `GET /users/heartbeat`
2. `POST /users/refresh` JSON: `refresh`
3. `POST /users/logout` JSON: `refresh`
4. `GET /users/me/notification-prefs`
5. `POST /users/me/notification-prefs` JSON: `channel, enabled?, offline_threshold_minutes?`
6. `POST /users/me/email-code` JSON: `email`
7. `POST /users/me/verify-email` JSON: `email, code, password`
8. `POST /users/me/contact-code` JSON: `channel, target`
9. `POST /users/me/bind-contact` JSON: `channel, target, code`
10. `GET /users/me/welcome-message`
11. `POST /users/me/welcome-message` JSON: `welcome_message`

## 7.4 Friendship 相关

1. `GET /friends/`：好友列表（返回用户数组）
2. `POST /friends/requests` JSON: `to_user_id`
3. `GET /friends/requests`：返回 `{ incoming, outgoing }`
4. `POST /friends/requests/respond?request_id=` JSON: `accept(0|1)`
5. `DELETE /friends/requests/remove?request_id=`

## 7.5 Chat 相关

1. `GET /chats/`：会话列表（含 `unread_count`, `last_read_at`）
2. `POST /chats/direct` JSON: `peer_user_id`
3. `POST /chats/group` JSON: `users, title`
4. `DELETE /chats/group?chat_id=`
5. `POST /chats/group/name?chat_id=` JSON: `title`
6. `POST /chats/group/members?chat_id=` JSON: `users`
7. `DELETE /chats/group/members?chat_id=` JSON: `users`
8. `GET /chats/group/invites`
9. `POST /chats/group/invite/respond?chat_id=` JSON: `accept(0|1)`
10. `POST /chats/read?chat_id=`

## 7.6 Message 相关

1. `GET /messages?chat_id=&limit=&before=&after=`
- `limit` 当前实现为必填，建议统一传 `30`
- `before/after` 二选一或都不传

2. `GET /messages/sync?after=&limit=`
- 作用：获取当前用户所有可见会话的全局新消息增量
- 说明：`after` 是全局 `message_id` 游标，建议首次传 `0`
- 返回：`{ items, has_more, next_after }`
  - `items` 中每条消息额外包含 `chat_id`

3. `POST /messages?chat_id=` JSON: `content, type`

4. `DELETE /messages?message_id=`

---

## 8. 前端数据模型（TypeScript 建议）

```ts
export enum AccountLevel {
  BASIC = 0,
  VERIFIED = 1,
}

export enum NotificationChannel {
  EMAIL = 1,
  SMS = 2,
  BARK = 3,
}

export enum FriendshipStatus {
  PENDING = 0,
  ACCEPTED = 1,
  REJECTED = 2,
  DELETED = 3,
}

export enum ChatType {
  DIRECT = 0,
  GROUP = 1,
}

export enum ChatMemberRole {
  MEMBER = 0,
  OWNER = 1,
}

export enum ChatMemberStatus {
  PENDING = 0,
  ACTIVE = 1,
  LEFT = 2,
  REJECTED = 3,
  KICKED = 4,
}

export enum MessageType {
  TEXT = 0,
  IMAGE = 1,
  FILE = 2,
  SYSTEM = 3,
}

export interface SpaceDTO {
  space_id: number;
  name: string;
  slug: string;
  email: string;
  email_verified_at: number | null;
  group_square_enabled: boolean;
  created_at: number;
}

export interface UserTinyDTO {
  user_id: number;
  name: string;
}

export interface UserDTO extends UserTinyDTO {
  is_alive: boolean;
  verified: boolean;
  last_heartbeat: number;
  email_verified_at: number | null;
  phone_verified_at: number | null;
  bark_verified_at: number | null;
}

export interface MessageDTO {
  message_id: number;
  user: UserTinyDTO;
  type: MessageType;
  content: string;
  created_at: number;
}

export interface MessageSyncItemDTO extends MessageDTO {
  chat_id: number;
}

export interface MessageSyncResponseDTO {
  items: MessageSyncItemDTO[];
  has_more: boolean;
  next_after: number;
}

export interface ChatDTO {
  chat_id: number;
  chat_type: ChatType;
  title: string | null;
  owner: UserTinyDTO | null;
  members: UserDTO[];
  group: boolean;
  created_at: number;
  last_chat_at: number;
  last_message: MessageDTO | null;
  unread_count?: number;
  last_read_at?: number | null;
}
```

---

## 9. 组件清单（可直接拆任务）

1. `AppShell`
2. `ChatList`
3. `ChatListItem`
4. `MessageList`
5. `MessageBubble`
6. `MessageComposer`
7. `FriendList`
8. `FriendRequestPanel`
9. `SpaceUserPanel`
10. `OnlineUserPanel`
11. `CreateGroupModal`
12. `InviteMembersDrawer`
13. `VerifyEmailFlow`
14. `BindContactFlow`
15. `NotificationPreferenceForm`
16. `AuthGuard`
17. `TokenRefreshInterceptor`
18. `HeartbeatAgent`

---

## 10. 可用性与无障碍

- 所有关键操作支持键盘触达。
- 对比度满足 WCAG AA（文本对比 >= 4.5:1）。
- 非文本状态（在线、错误）不能仅靠颜色表达，必须有图标/文案。
- 输入错误提示靠近字段，不只 toast。

---

## 11. 埋点与数据分析（最小集合）

事件建议：

1. `entry_space_create_submit`
2. `entry_space_join_submit`
3. `chat_open`
4. `message_send`
5. `friend_request_send`
6. `friend_request_accept`
7. `group_create`
8. `group_invite_send`
9. `email_verify_success`
10. `notification_pref_update`

公共字段：`space_id, user_id, timestamp, client_platform, network_type`。

---

## 12. 当前 API 缺口与前端处理建议

1. `GET /chats/group/invites` 当前返回 `ChatMember.json()`，缺少 `chat_id/chat_title`。
- 影响：前端无法在邀请列表中正确展示“来自哪个群”，也无法直接发起 `respond`。
- 建议后端补充：`chat_id`, `chat_title`。

2. 消息实时能力当前仅轮询，无 WebSocket。
- 影响：高频会话下体验一般。
- 前端策略：前台 2-3 秒轮询 + 输入中节流，后续可平滑升级到 WebSocket。

3. Space token 与 User token 的职责边界尚未完全前端化。
- 影响：若后续有 Space 管理后台，需新增 `SpaceAuthGuard`。
- 前端策略：当前用户端只以 User token 驱动业务页面。

---

## 13. 前端实施优先级（建议）

### P0（必须）

1. 进入流程（创建/登录/加入）
2. 会话列表 + 消息收发 + 已读
3. 好友体系（列表、申请、处理）
4. 用户设置（邮箱升级、通知偏好）
5. 心跳与 token 刷新

### P1（增强）

1. 群邀请中心完整化（依赖 API 补充）
2. 空间在线用户体验优化
3. 空态插画与主题皮肤

### P2（后续）

1. 图片/文件消息 UI
2. 通知中心 UI（事件流）
3. WebSocket 升级

---

## 14. 工程目录建议（前端）

```txt
src/
  app/
    router.tsx
    providers/
  modules/
    auth/
    space/
    user/
    friendship/
    chat/
    message/
    settings/
  shared/
    api/
    components/
    hooks/
    stores/
    styles/
      tokens.css
      theme.css
```

---

## 15. 验收标准（前端）

1. 新用户可在 3 分钟内完成“加入 Space -> 发首条消息”。
2. 任意会话消息首屏加载时间在常规网络下 < 1.5s（不含首包冷启动）。
3. token 过期后无感刷新成功率 > 99%（刷新 token 有效前提下）。
4. Basic 用户发起好友时有明确限制反馈且可跳转升级。
5. 移动端完整支持聊天核心流程（加入、发消息、查看好友申请）。
