# ds_qq

在本机 QQ 群里运行一个具备持久上下文、多模态识图和独立用量看板的 DeepSeek 机器人。

## 功能

- 通过 **NapCatQQ / OneBot 11 反向 WebSocket** 接入本机 QQ，只响应白名单群。
- 群内 **@机器人** 触发回复；普通群消息也会保存，作为后续对话上下文。
- 文本、QQ 表情占位、图片和贴纸识图；图片按真实格式校验并本地保存，不依赖会过期的 QQ 图片链接。
- 按群、按会话持久化用户消息和机器人回复；消息去重、同群串行处理、触发冷却、API 超时与有限重试。
- `/new` 切换会话但保留完整历史，`/stats` 查看存储统计，`/help` 查看帮助。
- 独立本地看板展示请求数、成功率、输入/输出/缓存 Token、缓存命中率、延迟、趋势、群/模型分布与成本估算。

## 架构

```text
本机 QQ + NapCat
       │ OneBot 11 反向 WebSocket（带 Token）
       ▼
dsqq-bot ───────────────► DeepSeek Chat Completions
   │                         文本 + 图片
   ├── data/ds_qq.db ◄──── dsqq-dashboard（独立进程）
   └── data/images/              │
                                 ▼
                       http://127.0.0.1:8080
```

运行依赖为 Python 3.11+、FastAPI、HTTPX 和 SQLite。机器人与看板只绑定回环地址，不需要公网服务器或反向代理。

## 1. 准备

1. 安装 macOS 版 QQ，并准备一个已加入目标群的 QQ 账号。建议使用专门的小号作为机器人，并自行评估第三方协议工具的账号风险。
2. 按 [NapCat 官方安装文档](https://napneko.github.io/guide/boot/Shell) 安装 NapCat。macOS 需先将 QQ 安装到 `/Applications/QQ.app`，再运行 NapCat Mac Installer。
3. 在 [DeepSeek 开放平台](https://platform.deepseek.com/) 创建 API Key。
4. 安装 [uv](https://docs.astral.sh/uv/)，然后安装项目依赖：

```bash
uv sync
cp .env.example .env
```

## 2. 配置

生成 NapCat 与机器人共用的随机 Token：

```bash
openssl rand -hex 32
```

编辑 `.env`，至少修改：

```dotenv
DEEPSEEK_API_KEY=sk-你的真实Key
ONEBOT_ACCESS_TOKEN=刚生成的随机Token
QQ_GROUP_IDS=目标群号
```

多个群用英文逗号分隔：`QQ_GROUP_IDS=123456789,987654321`。`.env` 和 `data/` 已被 Git 忽略，不要提交真实密钥或聊天数据。

默认使用 `deepseek-flash`，它支持文本与图片。模型、上下文窗口、图片大小、冷却时间及 Token 估算费率均可在 `.env` 调整。

## 3. 启动机器人

```bash
uv run dsqq-bot
```

另开终端检查：

```bash
curl http://127.0.0.1:3001/health
# {"status":"ok"}
```

保持该进程运行。日志出现 `NapCat connected` 才表示 QQ 插件已经连上。

## 4. 配置本机 NapCat

1. 启动 NapCat 并用机器人 QQ 扫码登录。
2. 打开启动日志给出的 NapCat WebUI 地址。默认是 `http://127.0.0.1:6099/webui/`，Token 以 NapCat 日志为准；首次登录后按提示修改密码。
3. 进入 **网络配置 → 新建 → WebSocket 客户端**（不是 WebSocket 服务端）：

| 配置项 | 值 |
| --- | --- |
| 名称 | `ds_qq` |
| URL | `ws://127.0.0.1:3001/onebot/v11/ws` |
| 消息格式 | `array` |
| Token | 与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 完全相同 |
| 上报自身消息 | 关闭 |
| 重连间隔 | `5000` ms |
| 心跳间隔 | `30000` ms |

4. 勾选 **保存时启用**，或保存后手动启用。NapCat 配置细节以 [官方 WebUI 指南](https://napneko.github.io/config/basic) 为准。

注意：`6099` 是 NapCat WebUI 默认端口，`3001` 是 ds_qq 的 OneBot 端口，两者不要混用。

## 5. 用本机 QQ 测试

回到机器人所在的群，用另一个账号依次测试：

1. `@机器人 你好，请介绍一下自己`，应收到引用回复。
2. 发送 `@机器人 我们刚才聊了什么？`，验证上下文。
3. 发送一张图片或表情，并在同一条消息中 `@机器人 描述这张图`，验证多模态识图。
4. 发送 `/stats`，确认保存条数增加；发送 `/new` 后再询问之前的内容，新会话不应带出旧上下文，但历史仍保留在数据库。
5. 在未加入 `QQ_GROUP_IDS` 的群里 @机器人，机器人应完全忽略。

## 6. 独立启动用量看板

机器人可以关闭，看板仍可查看已保存的数据：

```bash
uv run dsqq-dashboard
```

打开 <http://127.0.0.1:8080>。页面每 15 秒刷新，也可选择近 7/30/90 天趋势。成本根据 `.env` 中的本地费率估算，**请以 DeepSeek 官方账单为准**；看板不读取或展示聊天正文和 API Key。

## 数据与隐私

- `data/ds_qq.db`：完整消息、会话、附件索引和每次 API 用量。
- `data/images/`：按 SHA-256 去重保存的 JPEG/PNG/GIF/WebP 图片。
- 默认每次最多读取当前会话最近 30 条消息、24,000 字符和 4 张图片；数据库存全量历史，不代表每次都把全量历史发送给模型。
- 白名单群中的消息与图片会发送到 DeepSeek，用于上下文和识图。请先获得群成员同意。
- 如需彻底删除数据，先停止机器人和看板，再删除本地 `data/`。此操作不可恢复。

## 排错

- **NapCat 一直重连**：先启动 `dsqq-bot`；检查 URL 必须是 `/onebot/v11/ws`，并确认两边 Token 一致。
- **健康检查失败**：检查 `3001` 是否被其他程序占用，同时修改 `.env` 的 `BOT_PORT` 和 NapCat URL。
- **群里没有回复**：确认机器人已入群、群号在白名单、确实 @的是机器人账号，并查看终端中的 DeepSeek 错误。
- **只有图片时没有触发**：图片消息也需要 @机器人；单纯发图只保存，不调用 API。
- **图片识别失败**：确认使用 `deepseek-flash`，图片不超过 `BOT_MAX_IMAGE_BYTES`，并检查 QQ 图片下载日志。
- **看板没有数据**：两个进程需使用同一工作目录和 `DATA_DIR`；然后至少完成一次 @机器人调用。

## 开发检查

```bash
uv sync
uv run ruff check src/ds_qq
uv run python -m compileall -q src
uv build
```

项目参考：[DeepSeek 多模态图片输入](https://api-docs.deepseek.com/guides/vision/)、[DeepSeek 上下文缓存](https://api-docs.deepseek.com/guides/kv_cache/)。
