# 私聊、私聊定时与自动群聊

## 1. 好友私聊

在 `.env` 配置允许私聊的好友 QQ 号：

```dotenv
QQ_FRIEND_IDS=10001,10002
```

修改后重启 bot。本机持久部署可执行：

```bash
launchctl kickstart -k "gui/$UID/com.dsqq.bot"
```

白名单好友打开小号的私聊窗口，直接发送文字、图片、视频或 B 站链接即可，**不需要 @**。未列入白名单的账号会被完全忽略，不调用 DeepSeek。

每位好友拥有独立的短期会话和长期记忆，不会读取另一位好友的私聊。支持 `/new`、`/stats`、`/memory`；好友本人可用 `/forget` 清除自己的长期记忆，原始聊天仍保留。

## 2. 每日私聊定制消息

在 `scheduler.json` 中使用 `chat_type: private`，目标 `user_id` 必须同时在 `QQ_FRIEND_IDS` 中。

固定起床消息：

```json
{
  "id": "private-wake-up",
  "enabled": true,
  "chat_type": "private",
  "time": "08:30",
  "user_id": 10001,
  "message": "起床啦，先去洗把脸，再慢慢开始今天。"
}
```

AI 学习关怀：

```json
{
  "id": "private-study-check",
  "enabled": true,
  "chat_type": "private",
  "time": "20:00",
  "user_id": 10001,
  "prompt": "自然、关心地问对方今天有没有努力学习，不要审问，不要制造焦虑，控制在60字以内。"
}
```

任务按上海时区每天执行，配置保存后一分钟内生效。固定文案不调用 API；AI 文案结合人格和这位好友的私聊记忆。发送成功后写入对应私聊上下文，同一任务同一天不会重复发送。

## 3. 自动阅读和主动参与群聊

群消息始终会保存。默认只保存、不主动回复；如需机器人无 @参与话题，在 `.env` 显式开启：

```dotenv
BOT_AMBIENT_ENABLED=true
BOT_AMBIENT_PROBABILITY=0.08
BOT_AMBIENT_INTERVAL_MESSAGES=8
BOT_AMBIENT_COOLDOWN_MINUTES=20
BOT_AMBIENT_MAX_DAILY=10
```

默认策略同时满足以下条件后才按 8% 概率接话：

1. 距上次主动群聊至少 20 分钟。
2. 自上次主动群聊后至少积累 8 条消息。
3. 当天主动群聊少于 10 次。
4. 当前消息不是命令，也不是私聊。

主动回复使用 `ambient` 用途记录，看板显示“主动群聊”。发送失败不会推进状态；成功后才保存回复和冷却位置。

如果只想先观察效果，可临时设置 `BOT_AMBIENT_PROBABILITY=1`，其余限制保持不变。验证后建议调回较低概率，避免刷屏和 API 成本失控。
