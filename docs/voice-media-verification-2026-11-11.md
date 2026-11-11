# 2026-11-11 真实媒体联调记录

## 结果与范围

使用独立 SQLite 数据库、两名不同验证手机号的 VIP 好友测试账号；未修改生产用户、生产额度或发送外部通知。测试 API 与 watchdog 在开发机运行，LiveKit Server 1.13.11 在服务器运行，官方 Python RTC SDK 1.2.1 的两端通过认证 TURN 互传实际音频。

最初隔离测试使用 SSH/TLS 音频隧道；随后用户明确批准 443 TLS 分流，已配置独立 TURN 域名与有效证书。后续测试不使用媒体代理或音频隧道，通过正式公网 WSS/TURN 完成。最终还使用生产 API、MySQL 和受监管 watchdog 验证。真实 Chrome 访客窗口完成拨打、接听、麦克风收音、播放、静音、折叠/恢复和正常挂断；原浏览器配置未提供 WebRTC 能力，现已在拨打前检测并提示。

后端已在独立发布目录启用，前端最终版本为 `2026.11.11.9`。`sermo.jyonn.space`、`frienden.jyonn.space`、`sermo.6-79.cn`、`frienden.6-79.cn` 均已发布，部署工具验证 release.json、页面版本和脚本资源，并在 API release registry 中登记相同版本。iPhone PWA 实机和两个独立运营商网络尚未验收，不据此声称通过。

| 验证 | 实际结果 |
| --- | --- |
| 双端发送、接收 | 两端发布真实音频轨，分别收到 150 / 158 帧；解码主频 660Hz / 440Hz，RMS 5649 / 5661 |
| 正常挂断 | 接通后结算 2 秒；双方账本均为 used=2、reserved=0 |
| 到额断开 | 将独立测试接收方余额设为 6 秒；服务端结算 quota=6，两端媒体均断开 |
| 双方扣减 | 最终账本 used=8 / 300；reserved 均为 0 |
| 重复挂断 | 两端重复结束同一通话，没有额外扣费 |
| 旧令牌 | 正常结束及到额结束后，原媒体令牌入会均被 403 拒绝 |
| 对方无额度 | 返回 peer_quota_empty；不创建新通话消息，忙线占用均已释放 |
| 专项回归 | 25 项通话测试通过；Django check 通过 |
| MySQL 并发 | 独立 MySQL 验证库 27 项通过，包括同时拨打只预留一次、同时挂断只结算一次 |
| 生产 API 双向音频 | 两端分别收到 129 / 138 帧，正常结束各扣 2 秒；旧令牌返回 403 |
| 真实 Web 界面 | 麦克风实际音频帧到达对端；全屏、折叠、恢复、静音及接听通过；修复挂断竞争后正常结束 23 秒 |
| 生产到额断开 | 标准公网入口接通，6 秒后两端均被服务端断开；旧令牌 403 |
| 生产账本复核 | 测试用户最终 used=90 / 300，reserved=0；房间全部关闭、占用全部释放、每通一条消息，无外部通知投递 |

本轮发现并修复：DeleteRoom 需要 roomCreate 服务端权限；原生 SDK 使用 Bearer 请求头；媒体请求过期时必须先提交结算，再返回错误；watchdog 不应只扫描最早的 100 通未结束呼叫；无 WebRTC 能力的浏览器不创建邀请；主动挂断先静音并提交结算，再断开媒体，避免 participant_left 抢先写入断线结果。前端相关 19 项测试及生产构建通过。

## 可重复测试

`scripts/verify_voice_media.py` 使用官方 RTC SDK，生成 440Hz / 660Hz 信号并解码对端音频，核对双方余额变化与旧令牌失效。需要额外测试依赖 `livekit`、`numpy`、`requests`，不加入生产 Web 进程依赖。

凭证保存在权限 0600 的独立 fixture JSON 中，结构为 `chat_id` 和两个 `users` 对象，各包含 `id`、`auth`。只能使用专门授权的测试账号，禁止借用普通用户额度。脚本不生成账号、不修改数据库额度、不输出令牌。失败时仍尝试结束测试通话并断开媒体。

```sh
python scripts/verify_voice_media.py --fixtures /protected/voice-fixtures.json --api http://127.0.0.1:8903
# 先仅在测试库设置一方余额不超过 20 秒，再验证服务端自动断开：
python scripts/verify_voice_media.py --fixtures /protected/voice-fixtures.json --api http://127.0.0.1:8903 --wait-for-quota
```

`--media-test-url` 只用于隔离测试代理。带此选项的结果必须标为测试链路，不能作为公网验收。正式验收必须不带该选项，并额外覆盖真实 Web 界面、MySQL 行锁并发、不同网络和 iPhone 前台通话。

## 运维记录与待办

服务器原有可用内存不足、交换空间已满。最初增加隔离 Python 服务造成内存压力和连接超时；已停止全部隔离 Python 服务，将隔离测试逻辑移回开发机，增加独立 2GiB 交换文件恢复余量。原 API 恢复 200，原后端目录和普通用户数据保留。生产切换后交换文件已写入 fstab 持久化，旧 API 进程退出后可用内存约 848MiB。

已安装 Ubuntu 官方 Nginx stream 模块及匹配安全更新。用户批准后完成 443 SNI 分流，专用 `turn.sermo.jyonn.space` 转入认证 TURN；其余站点保留 HTTP TLS 处理链路和真实客户端 IP，原 API、时序、留幅及两个镜像检查通过。旧通配证书过期，已为 TURN 单独签发有效证书，并配置续期部署 hook。生产目录的未提交代码修改保留，旧自动拉取脚本与旧 Gunicorn 在新服务验证后停用。

生产服务为 `frienden-backend`、`frienden-voice-media`、`frienden-voice-watchdog`，均由 systemd 监管并设置开机启动与内存边界。额外 2GiB 交换文件已持久化；媒体 UDP 缓冲上限已调整。运行配置及发布目录见 [运维说明](foreground-voice-operations.md)，密钥不入 Git。

专用生产验收空间 `voice-rtc` 使用预留虚构号码，全部外部通知偏好关闭。清理获用户明确批准：停用两个测试账号、撤销测试会话、关闭测试空间聊天，保留记录。未修改普通用户的额度或账号。短信、邮件与外部即时推送的真实终端送达未实发，相关策略及投递回归通过；不将此描述为外部渠道实测送达。

参考：[RoomService 权限](https://docs.livekit.io/reference/other/roomservice-api/)、[自托管网络与 TURN](https://docs.livekit.io/transport/self-hosting/deployment/)。
