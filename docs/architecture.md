# ouzi-AI 架构设计

## 分层

- `static/`：前端页面，负责聊天界面、模型选择、语音输入、语音播放和波形展示。
- `app/main.py`：FastAPI 路由层，提供页面、配置、聊天、ASR、TTS 接口。
- `app/providers.py`：LLM provider 适配层，统一 DeepSeek、豆包、百炼和本地演示模型。
- `app/speech.py`：语音能力适配层，统一 ASR 与 TTS 引擎。
- `app/uploads.py`：图片上传、类型校验、本地存储和 data URL 转换。
- `app/schemas.py`：请求/响应模型，保证前后端契约清晰。
- `app/config.py`：环境变量配置，集中管理厂商密钥。
- `app/database.py`：SQLite 持久化层，管理会话、消息和审计记录。
- `app/metrics.py`：token 估算与模型费用统计。
- `app/tools.py`：外部工具层，提供当前时间、天气和联网搜索结果。

## API

### `GET /api/options`

返回模型、ASR、TTS 引擎列表，供前端下拉框渲染。

### `POST /api/chat`

请求：

```json
{
  "model": "deepseek-chat",
  "messages": [
    { "role": "user", "content": "今天天气怎么样？" }
  ],
  "temperature": 0.6
}
```

响应：

```json
{
  "model": "deepseek-chat",
  "provider": "deepseek",
  "reply": "今天北京天气晴朗，气温25°C，适合户外活动。"
}
```

### `POST /api/chat/stream`

使用 `text/event-stream` 流式返回：

- `meta`：会话 ID
- `token`：增量文本
- `done`：token 与费用统计
- `error`：错误信息

### `POST /api/uploads`

上传图片附件，返回附件元数据：

```json
{
  "attachments": [
    {
      "id": "xxx.png",
      "filename": "demo.png",
      "content_type": "image/png",
      "url": "/media/uploads/xxx.png",
      "size": 1024
    }
  ]
}
```

### `POST /api/sessions`

创建会话。

### `GET /api/sessions`

列出会话。

### `GET /api/sessions/{session_id}`

读取指定会话及聊天历史。

### `GET /api/audits`

读取最近模型调用审计记录。

## 数据表

### `chat_sessions`

保存会话标题、创建时间和更新时间。

### `chat_messages`

按会话保存 `system/user/assistant` 消息。

### `model_call_audits`

记录每次模型调用的 provider、模型、输入 token、输出 token、总 token、费用、耗时、状态和错误信息。

## Token 与费用统计

当前版本使用本地估算器：

- 英文/数字约按 4 个字符 1 token
- 中文等非 ASCII 字符约按 1.6 个字符 1 token
- 费用按 `app/metrics.py` 中每百万 token 的模型价格表计算

接入真实厂商后，建议优先使用厂商响应中的 `usage` 字段覆盖估算值。

### `POST /api/asr`

表单字段：

- `engine`：语音识别引擎
- `audio`：音频文件，可选

### `POST /api/tts`

请求：

```json
{
  "engine": "huoshan-tts",
  "text": "你好，我是你的 AI 助手。"
}
```

## 模型接入策略

当前 DeepSeek、豆包、百炼都走 `OpenAICompatibleProvider`。如果某个厂商接口参数不同，建议新增独立 provider 类，实现同样的 `chat()` 方法，路由层不需要改动。

视觉问答通过 `ChatRequest.attachments` 传入图片附件。后端在 provider 层把附件转为 `image_url` content part，格式兼容 OpenAI-style 多模态接口。纯文本模型会在路由层被拦截，避免把图片误发给不支持视觉的模型。

## 工具调用策略

后端在调用模型前会根据用户问题自动选择工具：

- 当前时间：本地工具，无需网络或 API Key
- 实时天气：通过 `wttr.in` 查询，适合演示用途
- 联网搜索：优先 Tavily，其次 SerpAPI

工具结果会被写入一条临时 `system` 消息，再交给模型回答。这样即使厂商接口暂未启用 function calling，也能让模型利用实时信息。流式接口会额外返回 `tool` 事件，前端统计栏会显示本次调用了哪些工具。

可选配置：

```env
ENABLE_TOOLS=true
TAVILY_API_KEY=...
SERPAPI_API_KEY=...
```

## 语音接入策略

前端优先使用浏览器原生能力降低演示门槛；后端保留 `/api/asr` 与 `/api/tts`，后续可接入火山引擎、阿里云智能语音、Azure Speech 等服务。

当前已实现的后端语音引擎：

- `baidu-asr`：百度智能云短语音识别，后端读取上传音频并调用 `vop.baidu.com/server_api`。
- `baidu-tts`：百度智能云短文本合成，后端调用 `tsn.baidu.com/text2audio`，保存 MP3 到 `data/audio/`，再通过 `/media/audio/...` 给前端播放。

百度语音鉴权：

1. 在 `.env` 配置 `BAIDU_API_KEY` 和 `BAIDU_SECRET_KEY`。
2. 后端使用 OAuth `client_credentials` 换取 `access_token`。
3. token 会缓存在内存中，临近过期时自动刷新。

## 后续可扩展点

- 增加文件/图片上传，实现视觉问答
- 增加 WebSocket 双向语音会话
- 将 provider 配置改为数据库或管理后台动态配置
