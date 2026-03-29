# Space IM - 产品需求文档（PRD v0.1）

- 文档版本：`v0.1`
- 文档状态：`Draft`
- 创建日期：`2026-03-10`
- 适用范围：`Sermo Backend` 重构为 `Space` 模型

## 1. 文档目的

本文件用于把当前分散需求统一为可执行产品规范，作为后续：

1. 数据模型改造
2. API 设计与实现
3. 测试用例与验收

的共同基准。

说明：PRD（Product Requirements Document）即“产品需求文档”。

## 2. 产品定义

### 2.1 产品定位

`Space IM` 是一个“空间内即时通讯系统”：

1. 用户可低门槛加入某个 `Space` 并快速与官方账号沟通。
2. 用户可逐步完善身份（Basic -> Verified）。
3. 用户可在空间内建立好友关系并进行私聊/群聊。
4. 空间拥有统一治理能力（官方账号 + 空间配置）。

### 2.2 核心目标

1. **低门槛接入**：仅昵称即可加入空间。
2. **渐进式注册**：加入后再补邮箱和密码，不阻塞首聊。
3. **可治理社交**：好友、群邀请、官方特权与通知留痕。
4. **在线可见性**：全员可见用户与在线状态（按空间配置规则）。

### 2.3 非目标（当前版本不做）

1. 跨 Space 的用户关系与聊天。
2. 音视频通话。
3. 消息撤回、已读回执到单条消息级别。
4. 复杂内容审核系统。

## 3. 角色与权限

### 3.1 角色定义

1. `Space Official`：空间官方账号（系统自动创建、特殊权限）。
2. `Basic User`：仅昵称加入，未验证邮箱/未设置密码。
3. `Verified User`：已验证邮箱并设置密码。
4. `Group Owner`：群创建者（可为任意用户，包括官方账号）。

### 3.2 核心权限差异

1. `Basic User` 不能主动发起好友申请。
2. `Basic User` 可以：
   1. 与官方账号聊天；
   2. 接收并同意他人好友申请；
   3. 成为好友后参与私聊。
3. `Verified User` 可以主动按昵称发起好友申请。
4. 官方账号拉群时，被邀请人**无需同意**直接入群（永久规则）。

## 4. 核心业务规则

### 4.1 Space 与用户

1. `Host` 概念升级为 `Space`。
2. 每个 `Space` 创建时自动生成一个官方账号 `official_user`。
3. 用户必须归属一个且仅一个 `Space`。
4. 昵称唯一性约束范围：`同一 Space 内唯一`。
5. 用户加入后，默认与官方账号建立好友关系。

### 4.2 加入与账号升级

1. 简洁加入：仅需昵称。
2. 可在加入时或加入后补充邮箱、验证邮箱、设置密码，升级为 `Verified User`。
3. `Basic` 和 `Verified` 的产品差异仅两项：
   1. 能否主动发起好友申请；
   2. 是否接收邮箱通知。

### 4.3 好友关系

1. 好友关系需通过申请/同意流程建立（官方默认好友关系除外）。
2. 用户可按“空间内昵称”发起好友申请（仅 Verified）。
3. 被申请方可同意/拒绝。
4. 建立好友后允许双方私聊。

### 4.4 群聊

1. 拉起群聊的用户为 `Group Owner`。
2. 普通用户拉群：被邀请用户需同意后入群。
3. 官方账号拉群：无需同意，直接入群（永久生效）。
4. 群主有成员增删权限。
5. 群消息当前仅文本，模型需预留图片/文件等消息类型。

### 4.5 可见性与在线

1. 空间内用户列表对全体用户全量可见。
2. 在线用户对全体用户可见。
3. 在线状态通过心跳判定，不依赖客户端本地标记。

### 4.6 通知

1. 所有消息相关通知（私聊、群通知、邀请通知等）必须记录到通知事件表。
2. 用户离线超过阈值后触发外部渠道通知。
3. 阈值支持：
   1. 用户可自定义；
   2. 渠道可配置不同阈值；
   3. 默认邮箱阈值为 30 分钟。
4. 除邮箱外需预留 SMS 等渠道能力（后续迭代）。

## 5. 功能模块拆解

### 5.1 Space 管理

1. 创建 Space。
2. 自动生成官方账号。
3. 官方账号可配置 Space 策略：
   1. 是否开启群广场；
   2. 其他空间级开关（预留）。

### 5.2 用户中心

1. 昵称加入。
2. 联系方式绑定与验证（邮箱为认证入口）。
3. 通知渠道管理（邮箱、SMS 预留）。
4. 心跳上报与在线状态维护。

### 5.3 社交关系

1. 好友申请发起（Verified only）。
2. 好友申请处理（同意/拒绝）。
3. 默认官方好友关系。

### 5.4 会话与消息

1. 私聊会话（好友关系后可聊；官方默认可聊）。
2. 群聊会话（邀请与入群状态管理）。
3. 消息发送/拉取（文本先行，类型可扩展）。

### 5.5 通知中心

1. 站内通知事件落库。
2. 离线检测与外部渠道投递。
3. 投递结果记录与重试（预留）。

## 6. 数据模型（逻辑层）

以下为建议逻辑模型，供数据库迁移设计：

1. `spaces`
   1. `id`
   2. `name`
   3. `slug`（唯一）
   4. `official_user_id`（创建后回填）
   5. `group_square_enabled`
2. `users`
   1. `id`
   2. `space_id`（索引）
   3. `nickname`
   4. `nickname_lower`
   5. `email`（可空）
   6. `email_verified_at`（可空）
   7. `password_hash`（可空）
   8. `role`（official / member）
   9. `account_level`（basic / verified）
   10. `last_heartbeat_at`
   11. 约束：`unique(space_id, nickname_lower)`
3. `friend_requests`
   1. `id`
   2. `space_id`
   3. `from_user_id`
   4. `to_user_id`
   5. `status`（pending/accepted/rejected/canceled）
4. `friendships`
   1. `id`
   2. `space_id`
   3. `user_a_id`
   4. `user_b_id`
   5. 约束：去重唯一（无向边）
5. `conversations`
   1. `id`
   2. `space_id`
   3. `type`（direct/group）
   4. `owner_user_id`（群主；私聊可空）
6. `conversation_members`
   1. `id`
   2. `conversation_id`
   3. `user_id`
   4. `join_status`（invited/accepted/rejected）
   5. `invited_by_user_id`
7. `messages`
   1. `id`
   2. `conversation_id`
   3. `sender_user_id`
   4. `message_type`（text/image/file/system）
   5. `content`
8. `notification_events`
   1. `id`
   2. `space_id`
   3. `user_id`
   4. `event_type`（new_dm/new_group_message/group_invite/...）
   5. `payload`
9. `notification_prefs`
   1. `user_id`
   2. `channel`（email/sms/bark/...）
   3. `enabled`
   4. `offline_threshold_minutes`
10. `notification_deliveries`
    1. `id`
    2. `event_id`
    3. `channel`
    4. `status`（pending/sent/failed/skipped）
    5. `attempted_at`

## 7. 状态机

### 7.1 用户账号状态

1. `BASIC`：仅昵称，不能主动加好友，不接收邮箱通知。
2. `VERIFIED`：已绑定并验证邮箱，可主动加好友，可接收邮箱通知。

迁移规则：

1. BASIC -> VERIFIED：通过 `bind-contact(channel=email)` 完成邮箱绑定验证。
2. VERIFIED -> BASIC：不支持（默认不可逆，避免权限与审计混乱）。

### 7.2 好友申请状态

`pending -> accepted/rejected/canceled`

### 7.3 群邀请状态

1. 普通邀请：`invited -> accepted/rejected`
2. 官方邀请：直接 `accepted`

## 8. 接口范围（v1 建议）

1. `POST /spaces`：创建空间（含官方账号）。
2. `POST /spaces/{slug}/join`：昵称加入空间。
3. `POST /users/me/contact-code`：发送联系方式验证码（支持 `channel=email/sms/bark`）。
4. `POST /users/me/bind-contact`：提交验证码并绑定联系方式（`channel=email` 会升级为 VERIFIED）。
5. `GET /users`：空间用户列表（全量可见）。
6. `GET /users/online`：在线用户列表。
7. `POST /friends/requests`：发起好友申请（Verified only）。
8. `POST /friends/requests/{id}/accept`：同意好友申请。
9. `POST /conversations/direct`：创建/获取私聊会话。
10. `POST /conversations/group`：创建群聊。
11. `POST /conversations/group/{id}/invite`：邀请入群。
12. `POST /conversations/group/{id}/respond`：受邀者同意/拒绝。
13. `GET /messages`、`POST /messages`：消息拉取/发送。
14. `POST /users/heartbeat`：上报心跳。
15. `GET/POST /users/me/notification-prefs`：通知阈值与渠道配置。

## 9. 非功能要求

1. 安全
   1. 密码仅存哈希；
   2. 邮箱验证有时效和重放保护；
   3. 关键接口限流（登录、申请、验证码）。
2. 性能
   1. 用户列表、在线列表支持分页；
   2. 心跳更新避免全表扫描。
3. 审计
   1. 好友申请、入群决策、通知投递均可追踪。
4. 可扩展
   1. 消息类型扩展不改核心表结构；
   2. 通知渠道可插拔。

## 10. 验收标准（MVP）

1. 用户可仅用昵称加入空间并立即与官方账号会话。
2. 同一空间内创建重复昵称会被拒绝。
3. Basic 用户无法发起好友申请；Verified 用户可以。
4. Basic 用户可以同意别人对自己的好友申请。
5. 非官方拉群时，被邀请人未同意前不可见群消息。
6. 官方拉群时，被邀请人直接成为群成员。
7. 全体用户均可查看空间用户列表与在线列表。
8. 在线状态由心跳 + 超时阈值正确变更。
9. 所有私聊/群消息事件均生成通知事件记录。
10. 默认邮箱离线阈值 30 分钟生效，且用户可修改。

## 11. 里程碑建议

1. `M1`：Space + Official + 昵称加入 + 心跳在线 + 私聊文本。
2. `M2`：账号升级（邮箱验证/密码）+ 好友申请。
3. `M3`：群邀请同意流 + 官方免同意特权。
4. `M4`：通知事件中心 + 离线渠道策略。

## 12. 风险与后续议题

1. 全量可见策略在大空间下的分页与隐私边界需持续评估。
2. 官方免同意拉群需要配套反骚扰策略（频控、黑名单、静音）。
3. 通知渠道扩展（SMS）涉及成本与地区合规，需单独方案。
