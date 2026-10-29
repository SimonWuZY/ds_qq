# 定时推送与扩展多模态

## 1. 每日群推送

创建本地任务文件：

```bash
cp scheduler.example.json scheduler.json
```

`scheduler.json` 被 Git 忽略。机器人约每 15 秒检查一次配置，保存后通常一分钟内生效，**无需重启 ds_qq 或 NapCat**。

### 固定群消息

```json
{
  "jobs": [
    {
      "id": "morning-group",
      "enabled": true,
      "time": "09:00",
      "group_id": 0,
      "message": "咕噜，早上好。别忘了喝水和休息。"
    }
  ]
}
```

### 每天 @固定群友并使用 AI 文案

```json
{
  "jobs": [
    {
      "id": "evening-friend",
      "enabled": true,
      "time": "21:30",
      "group_id": 0,
      "user_id": 10001,
      "prompt": "给这位群友一句自然、简短的晚安问候，可以结合最近的共同话题，但不要尬聊。"
    }
  ]
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `id` | 任务唯一标识，也是每日去重依据 |
| `enabled` | `false` 时停用任务 |
| `time` | 每天触发时间，`HH:MM`，按 Asia/Shanghai 时区 |
| `group_id` | 目标白名单群；只有一个白名单群时可填 `0` 自动选择 |
| `user_id` | 可选，在群里 @该成员；不填写则正常群发 |
| `message` | 固定文案，不调用 DeepSeek |
| `prompt` | AI 文案要求，结合人格、长期记忆和近期上下文生成 |

`message` 与 `prompt` 必须二选一。任务只允许白名单群，不支持私聊。NapCat 断线时会跳过，AI 推送成功后才写入群聊上下文，调用用途在看板中显示为“定时推送”。

同一任务同一天只发送一次，重启进程不会重复发送。修改任务时间或内容不需要改变 `id`；若希望创建一个全新任务，请使用新的 `id`。

查看执行日志：

```bash
# ds_qq 终端会显示 Sent scheduled job ...
docker logs -f dsqq-napcat
```

## 2. 图片、表情与视频

现有能力：

- JPEG、PNG、GIF、WebP 图片和贴纸通过 DeepSeek 多模态识别。
- 图片按真实格式校验、限制大小并本地去重保存。
- 视频通过本机 FFmpeg 抽取最多 **3 帧 JPEG**，再交给 DeepSeek 分析。

macOS 安装 FFmpeg：

```bash
brew install ffmpeg
```

视频限制：默认最大 100 MiB、最多 3 帧，下载、探测和抽帧均有超时。临时视频文件处理后会清理，抽出的帧按内容哈希保存在 `data/videos/`。

**视频能力不等于完整观看视频**：不会理解音轨、字幕或每一帧内容。需要精确到某句话或某个时间点时，仍应让群友补充文本。

可在 `.env` 调整：

```dotenv
BOT_MAX_VIDEO_BYTES=104857600
BOT_VIDEO_FRAMES=3
```

## 3. B 站链接

支持：

- `https://www.bilibili.com/video/BV...`
- `https://b23.tv/...` 短链

机器人会解析公开 API 中的标题、简介、UP 主和时长，作为文字背景交给 DeepSeek。默认每条消息最多处理 2 个链接：

```dotenv
BOT_MAX_BILIBILI_LINKS=2
```

**B 站能力目前不包括完整视频观看、弹幕、评论或字幕总结。** 链接解析失败时保留原始文字，不影响正常聊天。

## 4. 成本与安全

- 固定推送不产生模型调用；AI 推送和记忆整理都会产生额外 Token。
- 定时配置、人格文件、视频帧、登录态和聊天数据均不得提交到公开仓库。
- 推送频率应由你自行控制，避免打扰群友或触发 QQ 风控。
- 群聊和链接内容均是不可信数据，不能覆盖机器人系统规则。
