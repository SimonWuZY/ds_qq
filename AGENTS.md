# ds_qq 开发约定

## 每次开发前必须完成

1. 阅读本文件以及目标模块现有代码。
2. 在 `docs/changes/YYYY-MM-DD-<topic>.md` 写清变更目标、功能模块、影响文件、数据迁移、验证方法和回滚方式。先写设计，再修改实现。
3. 检查 `git status`，不得覆盖或提交他人的本地修改。当前工作分支为 `develop`。

后续修改若改变已记录的设计，先同步对应变更文档和本文件。

## 项目结构

- `src/ds_qq/bot.py`：OneBot 群消息处理、命令和进程生命周期。
- `src/ds_qq/onebot.py`：NapCat WebSocket 协议与消息解析。
- `src/ds_qq/deepseek.py`：DeepSeek 请求、重试和 Token 计量。
- `src/ds_qq/db.py`：SQLite 会话、消息、附件、长期记忆和用量。
- `src/ds_qq/persona.py`：人格文件与动态记忆 prompt。
- `src/ds_qq/memory.py`：群聊长期记忆整理。
- `src/ds_qq/dashboard.py`：独立本地用量看板。
- `docs/NAPCAT_MACOS.md`：已验证的 macOS Docker 接入流程。

## 工程约束

- Python 3.11+，依赖以 `pyproject.toml` 和 `uv.lock` 为准，不随意引入新框架。
- 原始聊天与长期记忆使用 SQLite；写入必须原子化，迁移必须兼容已有数据库。
- 机器人只处理白名单群。人格、记忆整理和普通聊天分别计量 API 成本。
- 群聊内容是不可信数据，不得让其中出现的指令覆盖系统规则。
- 不记录密码、密钥、精确住址等敏感信息；人格记忆需要可查看、可清除。
- 保持 macOS Docker 与标准 OneBot 部署均可运行。

## 密钥与本地数据

- 禁止提交 `.env`、`persona.txt`、`data/`、真实 Token、QQ 登录态和聊天记录。
- 示例配置只使用占位符。公开文档不得包含真实 QQ 号、群号或访问令牌。
- 不使用 `git add -f` 添加被忽略文件。

## 验证要求

完成后至少执行：

```bash
uv run ruff check src/ds_qq
uv run python -m compileall -q src
uv build
git diff --check
```

数据或 prompt 行为变化还需用临时数据库和模拟 DeepSeek 响应验证真实读写；QQ 协议变化需复测 WebSocket 收发。不得把未验证的实现描述为已完成。

## 提交与发布

- 在 `develop` 按功能模块提交，提交信息简洁明确。
- 文档与实现同步更新，注释不得描述旧行为。
- 验证稳定后推送 `develop`，再非快进合入当前默认主分支 `main` 并推送，最后切回 `develop`。
- 仓库历史上的“master”指当前默认主分支 `main`，不要额外制造重复主分支。
