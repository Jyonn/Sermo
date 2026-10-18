# 板凳聊天与通讯契约：待发布、待具体开通

本契约在 `oauth-chat-contacts-actions` 分支实现；线上 `.11` 尚未包含这些新增入口。当前总开关仍关闭、`supervision-web` 仍只批准 `email/profile`。不能将本地测试或本说明称为生产可用。

## 现状根因和本次范围

已实际查看提供的授权截图：只展示昵称/头像和邮箱，因为消费端真实请求仍只有 `profile email`。此前提供方也确实缺少第三方私聊创建、建群和群管理入口；`chat.write` 仅发文字、`chat.modify` 仅置顶。仅修改 scope 文案不能完成需求。

本次沿用两域的六个 scope，补真实受限入口及双语说明：

| scope | 真实动作 |
| --- | --- |
| chat.read | 当前空间会话/成员、文字历史、增量消息、当前可执行群动作、本人待处理群邀请和建群资格 |
| chat.write | 为已有好友创建/取得私聊；创建群（包括仅群主的研究组）；发送文字。无媒体发送/上传和任意账号冒充 |
| chat.modify | 本人置顶/已读；本人群邀请接受/拒绝；按现有角色邀请/移除成员、转让群主、改名、群背景、有限期群内禁言/解除、自己退群 |
| contacts.read | 本人好友、好友申请、已加入群；按精确账号名在当前空间查找，不枚举全部用户，不返回手机号/邮箱 |
| contacts.write | 向同空间既有账号发好友申请；沿用现有协管自动接受规则 |
| contacts.modify | 仅接受/拒绝本人收到的 pending 好友申请；不能替他人响应 |

依然排除：群永久删除、消息删除/撤回、永久禁言、平台角色/全局禁言、应用/插件管理、安全设置、通知/提醒设置、个人修改、投稿及广场新增 scope。业务动作按原规则产生站内通知，不等于授予通知读写能力。

## HTTP 协议（消费端可据此开发）

前缀 `/users/oauth/`；BFF 请求头为 `Authorization: Bearer <opaque access token>` 和 `X-OAuth-Client-ID: supervision-web`。不能换成普通 JWT 或把普通 JWT 交给第三方。所有写请求用 JSON，未知/缺失字段被拒绝。

| 方法 / 路径 | scope | 请求与结果 |
| --- | --- | --- |
| GET chat/capabilities | chat.read | `{create_group:boolean}`；与建群执行使用同一资格函数 |
| POST chat/direct | chat.write | `{peer_user_id:整数}` → `{chat:详情}`；须已有有效好友关系，重复调用返回同一有效私聊 |
| POST chat/groups | chat.write | `{user_ids:[整数…],title?:字符串}` → `{chat:详情}`；最多100个不重复 id，可空数组建仅群主研究组；受原建群/加入权限限制 |
| GET chat/chats | chat.read | 原列表接口，`{space_id,chats:[…]}`；保持 `.11` 列表形状兼容 |
| GET chat/chats/{id} | chat.read | `{chat:详情}`；只允许当前有效成员；退出/移除即时失效 |
| GET chat/chats/{id}/messages | chat.read | 原 before/limit 游标，最多100；本人可见文字历史 |
| POST chat/chats/{id}/messages | chat.write | 原 `{content,client_message_id}`；文字、内容检查、幂等、好友/禁言/群发言限制 |
| GET chat/chats/{id}/events | chat.read | 原 after/limit 增量游标；不是通知订阅或 WebSocket |
| POST chat/chats/{id}/preference | chat.modify | 原 `{pinned:boolean}`；仅自己置顶 |
| POST chat/chats/{id}/read | chat.modify | `{}` → `{chat_id,last_read_at}`；仅自己已读 |
| POST chat/chats/{id}/manage | chat.modify | 下表的严格命名动作；通常回 `{chat:详情}`；leave 回 `{chat_id,left:true}`，不返回退群后的成员数据 |
| GET chat/invitations | chat.read | `{invitations:[{chat_id,title,invited_by:{user_id,name}|null}]}`；只含本人的 pending 邀请，不返回会话内容 |
| POST chat/chats/{id}/invitation | chat.modify | `{accept:boolean}` → `{chat_id,accepted}`；只操作本人 pending 邀请，接受前重查加入政策 |
| GET contacts | contacts.read | 原 `{friends,requests,groups}`；只含本人关系和可见群名/id |
| GET contacts/search?name=精确账号名 | contacts.read | `{user:{user_id,name}|null,relationship:状态整数|null}`；须有原 contacts.search 能力，仅同空间精确匹配 |
| POST contacts | contacts.write | 原 `{peer_user_id}` → `{request:{request_id,peer,status,outgoing}}` |
| POST contacts/requests/{request_id}/respond | contacts.modify | 原 `{accept:boolean}` → 同上 request；仅本人作为接收者的 pending 申请 |

详情对象：`{chat_id,title,group,is_space_group,is_owner,background_theme,allowed_actions,members}`。成员为 `{user:{user_id,name},role,status,joined_at}`，role 0=成员、1=群主，status 1=有效。消费端应使用 `allowed_actions` 决定按钮显示，但每次执行仍可能因资格实时变化被拒绝。详情与旧列表的成员结构不同，不能直接假设同一 DTO。

错误使用 OAuth JSON：`error/error_description`，无效 token 通常401，缺少 scope 或业务政策拒绝403，无效字段400；管理员选择失败应同时检查 `admin_required` 等 error 值。建群和好友申请没有新增幂等键，结果不明时不得盲目自动重试；文字发送仍使用 client_message_id。

## 群动作与角色

manage JSON 必须精确符合一行，不允许混入其他字段。所有动作先要求当前空间的普通群、当前有效成员、空间聊天启用及当前群加入资格；不能操作投稿会话。

| action | 其他 JSON 字段 | 角色 / 业务限制 |
| --- | --- | --- |
| invite | user_ids:[id…]（非空） | 原 chat.group.invite 能力；目标为同空间未删除好友，并满足目标加入政策。沿用原模型：邀请通常立即成为 ACTIVE，不伪造必须待确认的流程 |
| remove | user_ids:[id…]（非空） | 仅当前群主；不得移除群主自己或其他群主角色。批量操作事务回退 |
| transfer_owner | peer_user_id | 仅当前群主，目标须为当前有效成员；不可给自己、不可跨空间、不可转让空间公共群 |
| rename | title（非空，最多50） | 沿原模型：当前成员有 chat.group.rename 能力；空间公共群另外要求空间管理员。并非所有群动作统一放宽给普通成员 |
| background | background_theme（字符串，可空） | 仅当前群主；只用原合法群主题及原外观资格，不支持 custom 上传或任意 URL |
| mute | peer_user_id,duration | 原群主/具有原管理资格的账号；不得禁言自己、管理员、群主；协管目标限制沿原模型。duration 仅10s/2m/10m/1h/6h/1d/7d，不接受 permanent |
| unmute | peer_user_id | 沿原模型：当前群主或空间协管；目标仍须为当前成员 |
| leave | 无 | 仅自己；群主须先转让群主，退群后 token 不能继续读取或管理该群 |

持有 chat.modify 不等于有群主资格；没有 chat.modify，即使本人是群主也不能经第三方入口管理群。原等级、密码、双认证和策略均保留，scope 不提供豁免。

## 导师、学生与已有成员

消费端统一请求 **`profile email chat.read chat.write chat.modify contacts.read contacts.write contacts.modify`**。

- 导师创建/绑定研究组：`purpose=space_admin` 可省略 space_id，先选择真正空间管理员账号。metadata 可展示尚未选定空间的冻结请求及全量分组；本人点同意时才冻结空间，检查当前 official_user、已批准 admin-proof capability、双认证，并签发同意范围内 token。userinfo 同时给出稳定 sub/space_id 和 space_admin=true。丢失当前管理员身份或客户端 proof capability，组合 token 即时不能继续使用。消费端不要先用 profile/email-only token 尝试创建聊天。
- 学生加入：`purpose=join`，必须传 BFF 已绑定/邀请冻结的整数 space_id；请求上面八项并本人同意。userinfo 不返回 space_admin。已有空间成员需保留相同 sub/space 绑定。
- 已有成员补授权：发起新的同空间请求，让本人明确点同意。不能向旧 OAuthGrant 填 scope、不能把旧 profile/email token 当新聊天 token，也不代替用户点同意。消费端先验证 token 返回的 scope 全集和 sub/space_id，再替换本人的旧 token。
- 管理员组合模式仅允许聊天/通讯六项，不把广场、投稿或个人修改混入管理员 bootstrap。普通固定空间授权仍保留原五域规则。
- access token 当前有效期仍为1小时；没有新 refresh-token/JWT 转换接口。过期应恢复本人授权流程，不能静默制造身份。

## 一次具体生产确认清单（尚未执行）

1. 发布本分支后端和配套前端 `.12`（四站），先保留新权限总开关关闭；无数据库结构变更。
2. 只为 `supervision-web` 创建并批准新 revision：原 `profile/email` + 本文六项，不含 phone 或其他域；原两条 HTTPS callback 精确保留：
   - `https://api.supervision.qijiong.work/api/auth/friendom/callback/`
   - `https://api.abandon.qijiong.work/api/auth/friendom/callback/`
3. 原 space_admin_proof_enabled=true 保留；不授予任何平台管理员资格。使用实时版本并经原审计/并发控制，不盲用历史版本8覆盖。
4. 确认后启用 `OAUTH_DELEGATED_ACTIONS_ENABLED=1`，其他客户端批准范围不变。总开关本身不授予 scope。
5. 开通前只读核实没有历史新域 grant/待处理请求；当前此前总开关始终关闭、批准客户端均为旧身份范围。若发现此前已同意窄版 chat.write/chat.modify 的 grant 或未完成请求，必须停止并单独处理，不把新增管理动作自动赋给旧窄授权。
6. 消费端同步导师、学生、补授权路径；真实用户自行同意。先做虚构 fixture 联调；本地单元测试不是生产用户授权成功。

回退先关闭总开关，再按审计版本撤回新增范围；不删除账号、群、好友、授权历史或数据库。现有 profile/email grant 保持原范围。

## 已验证与仍待完成

隔离 SQLite 内存数据库的98项后端测试通过，包含7项新增协作测试及原OAuth、五域和投稿回归。覆盖真实好友申请→接受→私聊创建/收发、建群、群主/成员隔离、转让、有限群禁言、批量回退、跨空间、资格报告与执行一致、导师组合授权和实时管理员身份失效。前端授权分组测试及构建通过。尚未做消费端BFF集成、真实浏览器或生产授权验收，也未修改任何生产客户端/开关。
