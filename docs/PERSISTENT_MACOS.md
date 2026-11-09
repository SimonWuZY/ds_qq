# macOS 持久运行与掉线恢复

## 推荐方案

五人小群优先使用现有 Mac，无需购买 VPS：

- NapCat 继续运行在 Colima Docker 中。
- Colima 通过 Homebrew services 在登录后启动。
- ds_qq 和看板通过 launchd 用户服务运行，开机启动、崩溃自动恢复。
- bot 由 `caffeinate -i` 包装，减少系统空闲睡眠。
- DeepSeek 按实际 API 用量付费，没有新增服务器月租。

如果以后需要真正的 24/7 云端运行，可迁移到 Linux VPS；但更换 IP 和设备可能增加 QQ 登录验证，不是当前性价比最高的选择。

## 安装自启动

标准本机 OneBot 部署：

```bash
bash deploy/install-macos-services.sh
```

Docker NapCat 连接部署（与本项目当前指南一致）：

```bash
DSQQ_ONEBOT_ACCESS_TOKEN=与容器onebot11一致的Token \
DSQQ_BOT_HOST=0.0.0.0 \
DSQQ_BOT_PORT=3002 \
bash deploy/install-macos-services.sh
```

脚本会：

1. 在 `~/Library/LaunchAgents` 生成 bot 和 dashboard 配置。
2. 校验并加载 `com.dsqq.bot`、`com.dsqq.dashboard`。
3. 注册 Colima 登录启动。
4. 将 `dsqq-napcat` 容器设为 `restart: unless-stopped`。

真实 Token 只进入本机 LaunchAgents，公开模板和 Git 不包含它。

## 验证与管理

```bash
curl http://127.0.0.1:3002/health
curl http://127.0.0.1:8080/health
launchctl print "gui/$UID/com.dsqq.bot"
launchctl print "gui/$UID/com.dsqq.dashboard"
docker ps
```

查看日志：

```bash
tail -f data/logs/bot.out.log
tail -f data/logs/bot.error.log
docker logs -f dsqq-napcat
```

修改 `.env` 或更新代码后重启服务：

```bash
launchctl kickstart -k "gui/$UID/com.dsqq.bot"
launchctl kickstart -k "gui/$UID/com.dsqq.dashboard"
```

卸载服务：

```bash
launchctl bootout "gui/$UID/com.dsqq.bot"
launchctl bootout "gui/$UID/com.dsqq.dashboard"
```

这不会删除 SQLite、图片或 NapCat 容器数据。

## 为什么仍会不在线

持久运行只能解决进程退出、Mac 重启和空闲睡眠，不能绕过 QQ 登录凭证失效。若日志反复出现：

```text
[NapCat] [Core] Login Error ... ErrCode: 3
```

需要打开 NapCat WebUI，用机器人小号重新扫码。为减少发生：

- 小号尽量只由这个 NapCat 实例使用，不在多个客户端互相顶号。
- 固定设备、网络环境和 Docker 数据目录，不频繁删除容器卷。
- 在 NapCat 登录配置中启用可用账号的快速登录；凭证失效时仍需人工扫码。
- 不运行会反复重启/顶号的其他守护脚本。
- 遇到账号风控时按 QQ 提示解除限制，不尝试绕过。

`caffeinate -i` 只减少空闲睡眠；合盖、断电、断网或手动睡眠仍会影响在线状态。长期运行时应接通电源并避免合盖睡眠。
