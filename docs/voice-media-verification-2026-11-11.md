# 2026-11-11 真实媒体联调记录

## 结果与范围

使用独立 SQLite 数据库、两名不同验证手机号的 VIP 好友测试账号；未修改生产用户、生产额度或发送外部通知。测试 API 与 watchdog 在开发机运行，LiveKit Server 1.13.11 在服务器运行，官方 Python RTC SDK 1.2.1 的两端通过认证 TURN 互传实际音频。

公网直连 ICE 尚未通过。TURN 的标准 443 TLS 分流改造被自动审批拒绝，等待用户明确批准。已通过的媒体测试使用专门的 SSH/TLS 测试隧道，不代表公网入口、浏览器麦克风、iPhone PWA 或跨运营商验收通过。生产通话开关及前端入口尚未启用，尚未发布新前端版本。

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

本轮发现并修复：DeleteRoom 需要 roomCreate 服务端权限；原生 SDK 使用 Bearer 请求头；媒体请求过期时必须先提交结算，再返回错误；watchdog 不应只扫描最早的 100 通未结束呼叫。

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

服务器原有可用内存不足、交换空间已满。最初增加隔离 Python 服务造成内存压力和连接超时；已停止全部隔离 Python 服务，将测试逻辑移回开发机，增加独立 2GiB 交换文件恢复余量。原 API 恢复 200，未更改生产后端代码目录或真实用户数据。交换文件当前只在运行时启用，后续应按容量评估决定是否持久化。

已安装 Ubuntu 官方 Nginx stream 模块及匹配安全更新，原配置检查通过；尚未执行全站 443 监听改造。生产目录的未提交代码修改已做差异核对，发布必须使用独立目录并保留原目录。

后续顺序：明确批准并验证公网 TURN 443 入口；独立 MySQL 并发与断线测试；发布后端和受监管 watchdog；两端 Web 联调及通知验证；最后启用前端通话并发布四个站点，核对 release registry。未经这些验证，不标记为上线完成。

参考：[RoomService 权限](https://docs.livekit.io/reference/other/roomservice-api/)、[自托管网络与 TURN](https://docs.livekit.io/transport/self-hosting/deployment/)。
