# 前台通话生产运维

生产后端代码在 `/home/ubuntu/frienden-backend-releases/<commit>`，当前运行版本可读 `/etc/frienden-voice/release.json`。原 `/home/ubuntu/Sermo` 目录及未提交修改保留；前端发布登记命令仍使用该兼容目录。不要直接删除旧目录或重新启动旧自动拉取循环。

## 服务与配置

| 服务 | 作用 | 入口 |
| --- | --- | --- |
| `frienden-backend` | API、媒体准入、签名 webhook、额度结算 | 127.0.0.1:6972 |
| `frienden-voice-media` | 官方 LiveKit 1.13.11 音频传输 | 127.0.0.1:7880；RTC TCP 7881、UDP 7882 |
| `frienden-voice-watchdog` | 每秒超时结算与房间关闭重试 | 无公网监听 |

systemd units 在 `/etc/systemd/system`。受保护配置在 `/etc/frienden-voice/backend.env`、`livekit.yaml`，证书副本在同目录，root 所有、ubuntu 组可读；不要将文件内容输出日志或提交 Git。API 使用公开 `wss://api.sermo.jyonn.space/voice-media`，管理 RPC 使用私有 `http://127.0.0.1:7880`。生产通知外发开关独立启用，仍尊重用户设置。

Nginx stream 在公网 TCP 443 读取 SNI：仅 `turn.sermo.jyonn.space` 转到认证 TURN TLS 5349，其余 TLS 连接转入回环 8443。PROXY protocol 将真实 IP 传入 HTTP 与 TURN。已有 HTTP 站点的回环 TLS 监听必须保留 `proxy_protocol`，不要绕过准入直接开放 7880。

媒体公开 HTTP 路由的 `auth_request` 指向 `/chats/voice/media-admission`，同时支持查询令牌与 Bearer 请求头。JWT 只能加入所属、尚未结束的通话。关闭房间不能替代此准入校验，自托管旧令牌可能在没有准入时重建房间。

证书由 Certbot HTTP-01 独立签发，HTTP 校验目录 `/var/www/frienden-voice-acme`，专用配置 `/etc/nginx/conf.d/frienden-voice-acme.conf`。`/etc/letsencrypt/renewal-hooks/deploy/frienden-voice.sh` 只处理 TURN 证书续期，将新证书复制到受保护配置目录后重启媒体服务；其他域名不触发该动作。

## 检查与后续发布

```sh
sudo systemctl is-active frienden-backend frienden-voice-media frienden-voice-watchdog
sudo nginx -t
sudo journalctl -u frienden-voice-watchdog --since '10 minutes ago'
```

不要把含 `access_token` 的完整请求地址或环境配置复制到工单。媒体访问日志关闭；诊断时仅保留路径、状态码和非敏感错误类型。

后续后端发布：准备新的独立目录，连接原 `.auth`、`mysql.local.conf` 及持久数据，执行 `manage.py check` 和迁移；更新 API 与 watchdog 的 WorkingDirectory；先验证私有端口健康，再检查公网与真实音频。四个原有定时服务（通知摘要、转码、数字对决结算、运营清理）当前同样指向独立发布目录，需要随版本同步。

回滚备份在 `/home/ubuntu/voice-stage/production-switch-backup`，包括 Nginx 和原 systemd units。旧 API 6970 已停用；如需切回，必须先在原目录启动旧 Gunicorn、验证 6970 健康，再恢复上游，不能只把 Nginx 改回一个未监听端口。新增通话迁移与旧表兼容，避免直接删除表或清空额度。

所有前端站点保持相同版本，按前端 AGENTS.md 发布两个 Pages 与两个镜像，并确认 `/frontend/releases` 四个 origin 的版本一致。当前部署流程以生产验收结果为发布依据；iPhone PWA 实机与独立运营商网络仍需补验收。
