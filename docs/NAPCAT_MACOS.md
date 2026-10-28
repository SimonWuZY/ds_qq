# macOS 使用 NapCat 接入 ds_qq

本指南将 DeepSeek 接入一个普通 QQ 账号所在的群聊，适用于 Apple Silicon 和 Intel Mac。**推荐 NapCat Docker 部署**，ds_qq 机器人与用量看板仍直接运行在 macOS。

## 1. 架构与准备

```text
手机 QQ 扫码授权
       │
       ▼
NapCat Docker（Linux arm64）
       │ OneBot 11 反向 WebSocket
       ▼
dsqq-bot（macOS）──► DeepSeek API
       │
       ├── data/ds_qq.db ──► dsqq-dashboard
       └── data/images/
```

准备事项：

- 一个普通 QQ 小号，已设置头像和昵称；不要优先使用主号。
- 主号已添加小号为好友，并把小号邀请进目标群。
- 已在项目 `.env` 配置 `DEEPSEEK_API_KEY`、`QQ_GROUP_IDS` 和 `ONEBOT_ACCESS_TOKEN`。
- 已执行 `uv sync` 和 `cp persona.example.txt persona.txt`；人格与长期记忆设置见[人格指南](PERSONALITY.md)。

NapCat 使用非官方 QQ 协议，存在账号风控风险。请使用小号并控制发言频率。

## 2. 安装并启动 Docker 运行时

推荐使用 Colima，无需 Docker Desktop：

```bash
brew install colima docker docker-compose
mkdir -p ~/.docker/cli-plugins
ln -sf /opt/homebrew/lib/docker/cli-plugins/docker-compose ~/.docker/cli-plugins/docker-compose
colima start --cpu 2 --memory 4 --disk 20 --vm-type vz
```

验证：

```bash
docker info
docker compose version
```

如果 GitHub 或镜像下载很慢，且本机已有可用 HTTP 代理，可在启动 Colima 前设置：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
export ALL_PROXY=http://127.0.0.1:7890
colima start --cpu 2 --memory 4 --disk 20 --vm-type vz
```

端口 `7890` 只是示例，应替换为你的实际代理端口；没有代理时不要设置这些变量。

## 3. 创建 NapCat 配置

以下命令生成两个独立 Token：一个用于 NapCat WebUI，另一个用于 NapCat 与 ds_qq 的 OneBot 连接。

```bash
cd /path/to/ds_qq
export NAPCAT_WEBUI_TOKEN=$(openssl rand -hex 16)
export DSQQ_DOCKER_TOKEN=$(openssl rand -hex 32)
mkdir -p data/napcat/data/{qq,config,plugins}
```

创建 `data/napcat/docker-compose.yml`：

```yaml
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: dsqq-napcat
    restart: unless-stopped
    environment:
      MODE: ws
      WEBUI_TOKEN: 替换为NAPCAT_WEBUI_TOKEN
    ports:
      - "127.0.0.1:6099:6099"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./data/qq:/app/.config/QQ
      - ./data/config:/app/napcat/config
      - ./data/plugins:/app/napcat/plugins
```

创建 `data/napcat/data/config/onebot11.json`：

```json
{
  "network": {
    "httpServers": [],
    "httpClients": [],
    "websocketServers": [],
    "websocketClients": [
      {
        "name": "ds_qq",
        "enable": true,
        "url": "ws://host.docker.internal:3002/onebot/v11/ws",
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "reconnectInterval": 5000,
        "token": "替换为DSQQ_DOCKER_TOKEN",
        "debug": false,
        "heartInterval": 30000
      }
    ]
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false
}
```

`host.docker.internal` 是容器访问 macOS 的地址。OneBot 使用反向 WebSocket，因此不需要把容器的 `3001` 或 `3002` 端口映射到宿主机。

## 4. 启动 ds_qq 容器连接端

```bash
ONEBOT_ACCESS_TOKEN="$DSQQ_DOCKER_TOKEN" \
BOT_HOST=0.0.0.0 \
BOT_PORT=3002 \
uv run dsqq-bot
```

这里使用 `3002`，避免与其他本地 NapCat 配置冲突。`BOT_HOST=0.0.0.0` 是为了让容器访问宿主机；连接必须由上面的随机 Token 鉴权，不要把该端口转发到公网。

另开终端验证：

```bash
curl http://127.0.0.1:3002/health
# {"status":"ok"}
```

## 5. 启动 NapCat 并扫码

```bash
docker compose -f data/napcat/docker-compose.yml up -d
docker logs --tail 80 dsqq-napcat
```

浏览器打开：

```text
http://127.0.0.1:6099/webui?token=替换为NAPCAT_WEBUI_TOKEN
```

在 WebUI 的“QQ 登录”页面点击二维码，使用手机上的**机器人小号**扫码并确认授权。登录成功后，ds_qq 终端应出现：

```text
NapCat connected
```

如果连接失败，检查容器日志中的反向 WebSocket 地址、Token，以及宿主机 `3002` 健康检查。

## 6. 第一次对话与验收

在目标群中，通过 QQ 的 @成员功能选择机器人小号，然后发送：

```text
@机器人 你好，请介绍一下自己
```

继续验收：

```text
@机器人 我们刚才聊了什么？
/new
/stats
/help
```

发送图片并在同一条消息中 @机器人：

```text
@机器人 描述这张图片
```

另开终端启动看板：

```bash
uv run dsqq-dashboard
```

访问 <http://127.0.0.1:8080>。完成一次对话后应出现请求数、Token、缓存和估算成本。

当前版本只处理白名单群聊，**私聊不会触发 DeepSeek 回复**。

## 7. 日常运维

```bash
# 查看状态与日志
docker ps
docker logs -f dsqq-napcat

# 重启 NapCat
docker compose -f data/napcat/docker-compose.yml restart napcat

# 停止 NapCat，保留登录与配置数据
docker compose -f data/napcat/docker-compose.yml down

# 再次启动
docker compose -f data/napcat/docker-compose.yml up -d

# 更新镜像
docker compose -f data/napcat/docker-compose.yml pull
docker compose -f data/napcat/docker-compose.yml up -d

# 停止或启动整个 Linux 虚拟机
colima stop
colima start
```

容器重启后可能需要重新扫码。QQ 登录态、OneBot 配置和插件分别保存在 `data/napcat/data/` 下，不要删除。

## 8. 为什么 macOS 原生 NapCat 不作为首选

部分 macOS + Apple Silicon + 新版 QQ 组合中，原生 NapCat 的 Hook 会修改 `wrapper.node` 的内存代码页，系统随后以代码签名无效强杀 QQ Helper。典型日志为：

```text
[NapCat] [UtilityProcess] Worker进程退出，退出码: 9
```

崩溃报告中可见：

```text
SIGKILL (Code Signature Invalid)
termination: CODESIGNING, Invalid Page
```

禁用 PacketBackend 也可能无法避开其他原生 Hook。Docker 使用 Linux 原生组件，可绕过 macOS 对该 Hook 的限制，因此是本指南的默认路线。

## 9. 安全与隐私

- `.env`、随机 Token、QQ 登录态和聊天数据均不得提交到公开仓库。
- 项目已忽略 `.env` 和 `data/`；不要使用 `git add -f` 强行添加。
- WebUI 只映射到 `127.0.0.1`，不要暴露到公网。
- 群消息和图片会发送到 DeepSeek，请确保群成员知情。
- 更换机器人账号只需在 WebUI 重新扫码；群白名单仍由 `.env` 的 `QQ_GROUP_IDS` 控制。
