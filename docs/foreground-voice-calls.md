# 前台语音通话：本地接入

2026-11-11 后续真实媒体与扣费验证见 [联调记录](voice-media-verification-2026-11-11.md)。以下保留最初本地接入范围；生产启用状态以联调记录为准。

当前代码未部署，生产功能开关默认关闭。通话页面全屏，可以折叠成应用内通话条。前端 `/design/voice-calls` 仅开发模式可访问，使用正式通话页组件和模拟数据，不申请麦克风、不发送通知；`design/foreground-voice-calls` 是上一轮独立方案原型。

## 已实现

- 普通好友私聊中的电话入口、拨打预检、麦克风授权、全屏呼出/来电/接通/结束、折叠/恢复、静音和音频播放恢复按钮。
- 后端统一检查双方永久 VIP、验证手机号、正常私聊权限、本人互拨、双方忙线和额度。每个验证手机号跨空间共用 300 秒，按北京时间周一零点重置。单次预留双方余额较小值，未接通释放预留，接通双方计时，整通向上取整到秒。
- 同一通话只有一条服务端生成的消息，`message.updated` 同步更新，不重复发送普通消息提醒。只允许参与者查询和控制；同一人跨设备/空间只能占用一通呼叫，接听设备绑定防止多端争抢媒体。
- 独立来电通知事件复用现有渠道、静音和隐私设置，即时投递，不进入普通离线摘要。过期/已接通的邀请不再投递。**本地通知外发单独关闭**，测试只验证入队逻辑。
- LiveKit 接入：仅音频发布、限定身份/房间的短期令牌、签名 webhook 校验、媒体服务确认双方麦克风就绪后开始计时、服务端 watchdog 超时结算与房间关闭重试。
- 进入后台或离开页面立即释放本机音轨并尝试结束，失联由服务端十秒超时兜底；未实现后台来电、系统 CallKit 或锁屏接听。

## 本地启动

不要将本地前端代理指向生产 API 来验证呼叫。以下配置使用 `/tmp` 中独立 SQLite 数据库，不读取生产配置或 OAuth 密钥。

```sh
# 后端目录，使用已经安装 requirements.txt 的 Python 环境
python manage.py migrate --settings=Sermo.voice_local_settings
python manage.py runserver 127.0.0.1:8901 --settings=Sermo.voice_local_settings
# 另一个终端：必须同时运行，否则无法保证无人在线时清理房间
python manage.py process_voice_calls --settings=Sermo.voice_local_settings
# 前端目录，另一个终端
FRIENDEN_DEV_API_TARGET=http://127.0.0.1:8901 npm run dev -- --host 127.0.0.1
```

界面评审：打开本地前端 `/design/voice-calls`，无需登录。真实媒体联调还需要本机 LiveKit 服务和两名本地 VIP 好友账号，双方已验证不同手机号。此页面不会把模拟用户变成真实账号。

LiveKit 配置对应环境变量：`VOICE_LIVEKIT_URL`、`VOICE_LIVEKIT_API_KEY`、`VOICE_LIVEKIT_API_SECRET`。本地 settings 提供的默认开发凭证只用于回环地址。LiveKit webhook 指向 `http://127.0.0.1:8901/chats/voice/webhook`，签名 API key/secret 必须一致。配置麦克风就绪事件 `participant_joined` / `track_published` 和离开事件。服务未就绪或 webhook 未接通会在连接超时后结束，不会冒充成功接通。

正式 settings 必须显式设置 `VOICE_CALLS_ENABLED=1` 才能创建，通知外发另设 `VOICE_CALL_NOTIFICATIONS_ENABLED=1`；生产前端默认隐藏入口，启用需 `VITE_VOICE_CALLS_ENABLED=1`。本次没有启用任何生产开关。

## 上线前仍需实测的边界

LiveKit SDK 与 REST 对接已写入，当前机器没有 LiveKit Server / Docker，尚未完成真实双端媒体联调。已完成的测试使用隔离 SQLite 和受控媒体响应，不应据此声称 iOS 真实通话通过。

自托管 LiveKit 的旧令牌撤销不同于 Cloud；DeleteRoom 能断开当前连接，但自托管旧令牌可能重建房间。令牌 TTL 不是已连接媒体的结束计时器。生产启用前必须验证到额服务端断开与旧令牌重入，选择具备有效撤销的 Cloud 或补准入控制；不能只凭页面自动挂断宣称严格防绕过额度。watchdog 需要进程监管和告警，媒体清理失败会持续重试。

真实 iPhone PWA 前台音频、麦克风权限、静音、后台释放和安全区；双端跨网络 TURN；多设备抢接；媒体服务不可达与 webhook 重试；MySQL 行锁并发与 watchdog 重启恢复，均属于上线前验证。本次不配置 TURN、不采购媒体服务、不部署或改生产数据。

参考：[令牌及撤销](https://docs.livekit.io/frontends/reference/tokens-grants/)、[RoomService](https://docs.livekit.io/reference/other/roomservice-api/)。

## 本次验证

隔离数据库迁移、`manage.py check`、清理任务单次运行通过；通话专项 21 项、原有通知策略/投递 10 项，以及前端消息映射/合并/通话更新 13 项通过。普通构建和开启通话开关的构建均通过，媒体 SDK 按需加载。Chrome 验证桌面与 390px 手机尺寸、深浅色、全屏/折叠/恢复、静音、到额自动结束及结束结果。

`npm run i18n:check` 仍报告基线已有的缺失项（简中 2 项，其他语言已有大量未翻译项）；逐语言比较未增加缺失键。新增文案提供简中/英文，其他语言有英文回退。`makemigrations --check --dry-run` 仍报告基线已有的两项旧索引命名及 OAuthGrant 元信息/主键漂移；在 HEAD 的临时归档副本复现相同操作。本次迁移仅包含通话表和通话/通知枚举，不混入这些旧模型调整。
