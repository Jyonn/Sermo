# 前台通话

2026-11-11 已完成真实媒体、生产双端扣费及浏览器界面验证，后端已启用。详见 [联调记录](voice-media-verification-2026-11-11.md) 和 [运维说明](foreground-voice-operations.md)。

通话页面全屏，可以折叠成应用内通话条。前端 `/design/voice-calls` 仅开发模式可访问，使用正式通话页组件和模拟数据，不申请麦克风、不发送通知；`design/foreground-voice-calls` 是上一轮独立方案原型。

## 已实现

- 普通好友私聊中的电话入口、拨打预检、麦克风授权、全屏呼出/来电/接通/结束、折叠/恢复、静音和音频播放恢复按钮。
- 后端统一检查永久 VIP、验证手机号、聊天权限、忙线和额度。每个验证手机号跨空间和私聊、群聊共用每周 30 分钟，按北京时间周一零点重置。私聊预留双方余额较小值，未接通释放预留，接通后双方计时，向上取整到秒。
- 普通群聊可发起或主动加入同一房间。LiveKit 确认每位成员的麦克风就绪后独立计时，成员离开时结算个人额度，最后一人离开时结束群通话。
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

群通话联调使用三个本地账号。三个账号应为同一普通群聊的活跃成员，分别绑定已验证手机号，并各有完整周额度。准备包含 `chat_id` 与 `users` 的本地 JSON，`users` 是三个含 `auth` 登录令牌的对象。运行 `python scripts/verify_group_voice_media.py --fixtures <本地凭据文件>`，脚本验证真实音频、发起人退出后其余两端继续互传、最后离开、旧凭据拒绝重入及各自的余额变化。

正式 settings 必须显式设置 `VOICE_CALLS_ENABLED=1` 才能创建，通知外发另设 `VOICE_CALL_NOTIFICATIONS_ENABLED=1`；前端启用需 `VITE_VOICE_CALLS_ENABLED=1`。生产已启用这些开关，运行时密钥只存放在服务器受保护配置中。内部 RPC 可通过 `VOICE_LIVEKIT_INTERNAL_URL` 指向回环地址。

## 真实验证与剩余边界

真实公网 WSS/TURN 双向音频、生产 API/MySQL 账本、到额服务端断开、正常挂断和旧令牌重入已验证；真实 Chrome 访客窗口完成拨打、接听、麦克风、播放、静音、折叠/恢复和正常挂断。普通 Chrome 配置的 WebRTC 支持被关闭，前端现于申请麦克风和创建邀请前检查此能力。

自托管 LiveKit 的旧令牌撤销不同于 Cloud；DeleteRoom 能断开当前连接，但旧令牌可能重建房间。现已通过 Nginx 准入校验绑定实际未结束的通话，结束后旧令牌返回 403。令牌 TTL 不是已连接媒体的结束计时器，服务端 watchdog 实际关闭到额房间；进程由 systemd 监管，清理失败持续重试。

真实 iPhone PWA 和两端独立运营商网络尚未验收；未实现后台来电和锁屏接听。通知策略及投递回归通过，验收账号关闭外部通知，未实际向短信、邮件或其他即时推送终端发送测试消息。

参考：[令牌及撤销](https://docs.livekit.io/frontends/reference/tokens-grants/)、[RoomService](https://docs.livekit.io/reference/other/roomservice-api/)。

## 本次验证

隔离数据库迁移、`manage.py check`、清理任务单次运行通过；MySQL 通话专项与并发共 27 项、通知策略/投递 10 项，以及前端媒体预检、挂断顺序、菜单和消息映射/合并共 19 项通过。生产构建通过，媒体 SDK 按需加载。此前界面验证覆盖桌面与 390px 手机尺寸及深浅色；本次补齐真实浏览器通话与生产服务端扣费。

`npm run i18n:check` 仍报告基线已有的缺失项（简中 2 项，其他语言已有大量未翻译项）；逐语言比较未增加缺失键。新增文案提供简中/英文，其他语言有英文回退。`makemigrations --check --dry-run` 仍报告基线已有的两项旧索引命名及 OAuthGrant 元信息/主键漂移；在 HEAD 的临时归档副本复现相同操作。本次迁移仅包含通话表和通话/通知枚举，不混入这些旧模型调整。
