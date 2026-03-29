# JY Backend Style Guide

这份文档用于总结当前 `Sermo` 后端所体现出的、具有明显个人风格的设计方式。目标不是机械复述当前仓库里的每个细节，而是提炼出一套“今后其他项目也应优先遵循”的后端写法。

如果未来要让 Codex 或其他协作开发者学习你的后端风格，应优先遵循本文件，而不是逐字模仿仓库里所有历史遗留实现。

## 1. 风格总纲

你的后端风格不是 Django 默认风格，也不是传统“一个 app 一个大文件”的风格，而是：

1. 按业务域拆 App，而不是按技术层或通用实体拆。
2. 每个 App 内部继续按职责拆层：`models / params / validators / views / urls`。
3. 明确区分“数据结构”“输入校验”“业务状态机”“接口返回”。
4. 偏好轻量、清晰、直接的业务表达，不喜欢为了“通用性”而提前抽象过度。
5. 偏好用 `smartdjango` 建立统一范式，而不是使用原生 Django 的零散习惯拼接。
6. API 设计偏业务语义，不追求 REST 教条。
7. 返回 JSON 时强调“最小必要信息”，不同场景应有不同粒度的 `json/jsonl/json_me/...`。
8. 重视状态机、权限边界、真实业务约束，胜过抽象的“模型优雅”。

一句话概括：

> 以业务域为核心，用 `smartdjango` 建立一套强约束、低冗余、可读性高、状态机明确的后端分层。

## 2. App 划分风格

你的 App 划分具有明显的业务导向：

1. `Space`
2. `User`
3. `Friendship`
4. `Chat`
5. `Message`
6. `Config`

这说明你的习惯不是把“用户、聊天、消息”混成一个大社交 App，而是让每个业务对象单独成域。

### 你的偏好

1. 核心业务对象单独建 App。
2. 同一类职责集中在一个 App 中解决，不把强业务逻辑散落到 util 里。
3. 允许领域之间相互引用，但引用应服务真实业务关系，而不是为了“纯净架构”强行隔离。

### 未来项目建议

如果一个对象已经是系统里的一级业务概念，就应该考虑独立 App，而不是塞进一个“common/social/core”。

## 3. App 内部文件结构风格

你明显偏好这种结构：

1. `models.py`
2. `params.py`
3. `validators.py`
4. `views.py`
5. `urls.py`

当一个 App 内有多个表或多个子概念时，你不喜欢共享一套模糊的 params/validator，而是倾向于：

1. 一个 `Model` 对应一个 `Params`
2. 一个 `Model` 对应一个 `Validator`
3. 如果一个文件中放多个模型，也最好有各自明确的 params/validator 入口

例如你之前强调过：

1. `Friendship` 要有自己的 `Model / Param / Validator`
2. `Chat` 和 `ChatMember` 即便在同一个模型文件里，也应分别有 `chatparams / chatmemberparams`、`chatvalidator / chatmembervalidator`

### 这背后的风格本质

不是“文件越多越好”，而是：

> 不同表、不同职责、不同输入边界，必须在命名和结构上显式区分。

## 4. smartdjango 使用风格

你当前后端最鲜明的个性之一，就是把 `smartdjango` 当作主框架习惯，而不是辅助工具。

### 4.1 Model 风格

业务模型优先使用：

```python
import smartdjango.models as models
```

而不是默认：

```python
from django.db import models
```

当前仓库里 `Config` 仍有历史例外，但从你的明确要求看，未来风格应以 `smartdjango.models` 为准。

### 4.2 Params 风格

你强烈偏好 `Params` 元类驱动的声明式输入校验。

典型模式：

```python
class SpaceParams(metaclass=Params):
    model_class = Space

    slug: Validator
    name: Validator
    email: Validator
```

你的明确偏好是：

1. 如果字段类型和 validator 已经能从 model 推导，就直接写 `field: Validator`
2. 不要重复写 `.to(str) / .bool(...)` 这种模型里已经表达过的东西
3. 只有当请求参数和模型字段不完全一致时，才额外写 `Validator(...)`
4. 复用已有规则时，优先 `copy().rename(...)`

例如：

```python
new_password = UserParams.password.copy().rename('new_password')
```

而不是重新手写一套密码验证逻辑。

### 4.3 analyse 风格

你的接口输入风格非常清晰：

1. `@analyse.json(...)` 处理 JSON body
2. `@analyse.query(...)` 处理 query 参数
3. 参数对象在 decorator 层显式列出

这意味着：

> 输入边界应写在 View 方法签名外部，而不是在函数体里零散取值。

### 4.4 Error 风格

你偏好 `@Error.register` 集中管理错误，而不是到处 `raise ValueError(...)`。

错误的特点：

1. 错误属于域，例如 `UserErrors`、`SpaceErrors`
2. 错误是业务语义，不是技术细节
3. 错误文案支持 i18n
4. 错误码要和场景匹配：`BadRequest / Forbidden / NotFound / Unauthorized / InternalServerError`

## 5. Model 写法风格

你的 Model 不只是 ORM 结构体，而是承载核心业务规则的地方。

### 5.1 偏好把业务动作写成 classmethod / method

例如：

1. `Space.create(...)`
2. `Space.login_by_email_code(...)`
3. `User.create(...)`
4. `User.login(...)`
5. `Friendship.create(...)`
6. `Friendship.accept(...)`
7. `Chat.get_or_create_direct(...)`
8. `Message.sync_for_user(...)`

这说明你的风格是：

> 真正的业务动作应该进入模型层，而不是只放在 view 层拼 ORM。

### 5.2 偏好显式 index/get_by_xxx

例如：

1. `User.index(user_id)`
2. `Chat.index(chat_id)`
3. `Space.get_by_slug(slug)`

而不是在每个 view 里重复写：

```python
Model.objects.get(...)
```

这种做法的价值是：

1. 查询入口统一
2. 错误语义统一
3. 业务过滤统一，例如 `is_deleted=False`

### 5.3 偏好在 Model 内做归一化

你很常做：

1. `strip()`
2. `lower()`
3. 统一处理空值
4. 统一做名称和 slug 归一化

例如：

1. `slug = (slug or '').strip().lower()`
2. `email = (email or '').strip().lower()`
3. `name = name.strip()`

这说明你不喜欢把“清洗输入”分散到 view 和前端，而更倾向于在领域方法入口统一处理。

### 5.4 偏好状态机显式化

你经常用 `Choice` 类把状态定义清楚：

1. `UserAccountLevelChoice`
2. `UserRoleChoice`
3. `FriendshipStatusChoice`
4. `ChatTypeChoice`
5. `ChatMemberStatusChoice`
6. `MessageTypeChoice`

同时，状态变化通过显式方法完成，例如：

1. `accept`
2. `reject`
3. `remove`
4. `leave`
5. `bind_contact`

你的风格不是让调用方随便改状态字段，而是：

> 通过命名明确的方法驱动状态迁移。

## 6. JSON 返回风格

这是你风格里非常重要的一部分。

### 6.1 你不喜欢“一套 JSON 打天下”

你倾向于按场景提供不同层级的 JSON：

1. `tiny_json()`
2. `jsonl()`
3. `json()`
4. `json_me()`
5. `json_friend()`
6. `json_private()`

这说明你非常在意：

1. 返回最小必要字段
2. 不把隐私字段和大字段到处泄漏
3. 不同页面和接口应该拿不同粒度的数据

### 6.2 你的推荐原则

1. 默认使用最小 JSON
2. 只有“当前用户查看自己”时才返回私有字段
3. 列表接口返回 `jsonl`
4. 内嵌对象返回 `tiny_json`
5. 特定业务场景单独增加 `json_xxx`

### 6.3 你的字段组织偏好

你偏好：

1. 统一用 `_dictify_xxx` 处理派生字段
2. 时间戳统一返回 `timestamp()`
3. 主键常常重命名成业务语义字段，如 `id->user_id`, `id->chat_id`

这是一种很强的接口整洁意识。

## 7. View 写法风格

你的 View 层追求的是“薄而清楚”。

### 7.1 View 的职责

View 主要做四件事：

1. 鉴权
2. 参数接收
3. 调用模型方法
4. 组织返回

你不喜欢在 View 里堆很多 ORM 查询和业务分支。

### 7.2 View 的典型风格

一个标准接口通常长这样：

1. `@auth.require_user`
2. `@analyse.query(...)` 或 `@analyse.json(...)`
3. 调用一个模型方法
4. 返回 `dict(...)` 或 `model.json()`

例如这是你的典型审美：

```python
class SpaceMeView(View):
    @auth.require_user
    def get(self, request: Request):
        return request.user.space.json()
```

很短，但职责非常清晰。

### 7.3 允许 View 内保留少量“接口辅助逻辑”

例如：

1. `_extract_client_ip`
2. `_require_password_enabled`

这类函数不是领域核心，更像接口保护或请求适配，因此放在 view 附近是符合你风格的。

## 8. URL 风格

你的 URL 设计风格非常实用主义。

### 8.1 不追求 REST 教条

你接受如下路径：

1. `/friends/requests/respond`
2. `/friends/requests/remove`
3. `/users/me/contact-code`
4. `/users/me/bind-contact`
5. `/messages/sync`

这些都是业务动作路径，而不是纯资源路径。

### 8.2 路径短、动作清晰

你更在意：

1. 前端一眼能懂
2. 业务动作名明确
3. 查询参数设计简单

例如你会主动把 `friendship_id` 改成 `user_id`，因为对调用方更自然。

### 8.3 `me` 是你的重要语义前缀

当接口以当前用户为主体时，你偏好：

1. `/users/me`
2. `/users/me/password`
3. `/users/me/contact-code`
4. `/users/me/notification-prefs`

这说明你喜欢让 URL 直接表达“当前登录用户上下文”。

## 9. Validator 风格

你的 validator 不是“形式主义补充”，而是业务约束的集中声明。

### 9.1 偏好常量放在 Validator

例如：

1. `NAME_MAX_LENGTH`
2. `PASSWORD_MIN_LENGTH`
3. `WELCOME_MESSAGE_MAX_LENGTH`
4. `SLUG_MAX_LENGTH`

这说明你喜欢让“字段规则”靠近 validator，而不是散落在 settings、forms、serializers、views 中。

### 9.2 偏好业务校验方法

例如：

1. `slug(value)`
2. `language(value)`
3. `welcome_message(value)`
4. `avatar_preset_id(value)`

你的风格是：

> validator 负责表达“这个字段从业务上是否合法”，而不仅仅是类型是否合法。

## 10. Params 风格细则

这是你后续最值得让 Codex 学会的一部分。

### 10.1 优先复用模型定义

如果模型已定义字段类型和 validator，优先直接写：

```python
slug: Validator
email: Validator
code: Validator
```

### 10.2 需要变形时再 copy/rename

例如：

```python
old_password = UserParams.password.copy().rename('old_password')
new_password = UserParams.password.copy().rename('new_password')
```

### 10.3 一个 Model 对应一个 Params 类

不是“一个 View 一个 Params”，而是：

> 一个模型类对应一个参数类，View 只是组合使用这些参数。

### 10.4 避免重复校验

如果规则已经在 model validator 中存在，就不要在 params 里再手写一次相同规则。

## 11. 业务建模风格

你的建模方式有几个稳定特征。

### 11.1 关系建模强调“真实业务语义”

例如：

1. 好友关系单独建 `Friendship`
2. 会话单独建 `Chat`
3. 成员关系单独建 `ChatMember`

你不会为了省表而把关系硬塞进一个模型。

### 11.2 偏好“状态 + 行为”组合

例如：

1. `Friendship` 既存状态，也提供 `accept/reject/remove`
2. `ChatMember` 既存状态，也承载 `invite/respond/kick`

### 11.3 偏好 lazy delete 而不是硬删

例如：

1. `User.is_deleted`
2. `Chat.is_deleted`
3. `Message.is_deleted`
4. `Friendship.status = DELETED`

这反映出你重视：

1. 审计轨迹
2. 历史消息保留
3. 后续恢复与状态判断

## 12. 权限与约束风格

你的权限控制很少依赖“接口名字约定”，而是通过模型方法和 auth helper 双重表达。

例如：

1. `@auth.require_chat_member()`
2. `chat.has_active_member(user)`
3. direct chat 还要进一步检查 friendship 是否仍是 `ACCEPTED`

也就是说：

> 表面成员资格不够，真实业务关系也要再次验证。

这是你后端里很有辨识度的一点。

## 13. 配置与外部服务风格

你不喜欢把外部服务配置散落在环境变量读取逻辑里，而是更偏好：

1. 独立 `Config` 模型
2. 用固定 key 管理配置
3. 用全局 helper 暴露业务侧需要的配置值

此外，你对外部服务调用的风格是：

1. 优先直接使用项目约定好的客户端实例
2. 不写过度兼容的冗余 fallback
3. 文案和过期时间等业务信息应来自真实常量，而不是硬编码

## 14. 文档风格

你的文档偏产品与实现并重。

已有文档说明你在意：

1. PRD
2. 前端设计规范
3. 集成说明
4. i18n 检查清单

这意味着你的工程风格不是“代码写完就算了”，而是：

> 业务、接口、前端、外部服务，都应该有可追踪的文档沉淀。

## 15. 你的后端风格中最重要的“不要这样写”

如果未来要模仿你的风格，以下做法应尽量避免：

1. 不要把多个业务实体揉进一个超大 App。
2. 不要在 View 里直接写大段 ORM 和业务状态流转。
3. 不要让不同模型共享一套模糊的 params/validator。
4. 不要为了“通用性”写大量你当前根本不用的兼容代码。
5. 不要把所有接口都返回同一份大 JSON。
6. 不要重复声明模型里已经能表达的字段规则。
7. 不要把真实业务关系偷懒建模成临时字段。
8. 不要把对调用方更自然的 `user_id` 又包装成抽象的内部 id。
9. 不要默认相信表面成员关系，必要时要二次校验业务状态。
10. 不要写硬编码业务常量，尤其是过期时间、配置项、通知内容来源。

## 16. 未来项目中，Codex 应如何按你的风格写后端

下面这段可以直接作为后续项目的协作约束：

### 16.1 架构层

1. 先按业务域拆 App。
2. 每个 App 默认建立 `models.py / params.py / validators.py / views.py / urls.py`。
3. 复杂业务优先写进 Model 方法，不要先堆到 View。

### 16.2 输入层

1. 所有接口参数优先走 `smartdjango.Params + analyse`。
2. 模型已有规则就直接复用，不重复写类型与校验。
3. 参数重命名优先使用 `.copy().rename(...)`。

### 16.3 模型层

1. 用 `Choice` 建状态枚举。
2. 用显式方法表达状态迁移。
3. 用 `index/get_by_xxx/between/...` 提供统一查询入口。
4. 在模型入口统一做 `strip/lower/default` 等归一化。

### 16.4 返回层

1. 默认最小返回。
2. 列表、轻量对象、当前用户私有对象分别使用不同 JSON 方法。
3. 内嵌对象使用更小粒度的 JSON。

### 16.5 API 层

1. API 按业务动作命名，不强求 REST。
2. 优先让前端调用自然，参数命名偏业务语义。
3. 当前用户语境优先使用 `/me`。

### 16.6 工程层

1. 错误统一在域级 `Errors` 中定义。
2. 文案支持 i18n。
3. 避免写冗余兼容代码和无用抽象。
4. 文档应同步更新。

## 17. 这份风格指南的解释优先级

未来如果“仓库现有实现”和“本文件中的主风格”不完全一致，应按以下优先级理解：

1. 用户明确表达过的偏好
2. 本文件总结出的主风格
3. 当前仓库中重复出现的稳定模式
4. 个别历史遗留实现

也就是说：

> `Config` 这类历史例外不应被当成你的主风格模板。

## 18. 最终总结

你的后端风格可以概括成四句话：

1. 以业务域拆分系统，而不是以技术层堆叠系统。
2. 用 `smartdjango` 建立统一、强约束、低冗余的输入与错误处理方式。
3. 把业务状态机和关键权限放在模型层，而不是零散写在接口层。
4. 让 API 和返回结构服务真实产品需求，而不是追求抽象上的“标准化”。

如果未来让 Codex 学你的风格，最重要的不是记住某个函数名，而是记住你的审美：

> 结构清楚、边界明确、命名直白、状态真实、返回克制、拒绝冗余。

