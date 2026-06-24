# 校园服务智能助手架构设计

## 分层

- `static/`：前端页面，负责聊天界面、模型选择、语音输入、语音播放和波形展示。
- `app/main.py`：FastAPI 路由层，提供页面、配置、聊天、ASR、TTS 接口。
- `app/providers.py`：模型目录层，描述可选模型及其能力标签。
- `app/scenario.py`：场景配置层，集中维护服务台提示词、快捷问题和前端场景文案。
- `app/auth.py`：认证鉴权层，负责密码哈希、Token 签发、登录用户和管理员权限校验。
- `app/knowledge.py`：知识库层，负责文档上传、文本抽取、切块和混合检索。
- `app/langchain_runtime.py`：LangChain 运行时层，负责消息转换与模型调用。
- `app/speech.py`：语音能力适配层，统一 ASR 与 TTS 引擎。
- `app/uploads.py`：图片上传、类型校验、本地存储和 data URL 转换。
- `app/schemas.py`：请求/响应模型，保证前后端契约清晰。
- `app/config.py`：环境变量配置，集中管理厂商密钥。
- `app/database.py`：SQLite 持久化层，管理会话、消息和审计记录。
- `app/metrics.py`：token 估算与模型费用统计。
- `app/tools.py`：外部工具层，提供当前时间、天气和联网搜索结果。

## API

### `GET /api/options`

返回模型、ASR、TTS 引擎列表和当前场景配置，供前端下拉框与快捷问题渲染。

### `POST /api/login`

登录接口，校验本地用户账号密码后返回 Bearer Token。前端把 token 存入 `localStorage`，后续请求通过 `Authorization` 请求头传入。

### `GET /api/me`

返回当前登录用户信息，用于页面初始化时恢复登录态。

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

### `POST /api/knowledge/upload`

上传知识库文件，支持 `txt`、`md`、`pdf`、`docx`。后端会保存文件、抽取文本、切块并写入 SQLite。

### `GET /api/knowledge`

返回已入库文档列表，供前端展示资料数量和最近上传文件。

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

### `knowledge_documents`

保存知识库文件名、类型、存储路径、切块数量和创建时间。

### `knowledge_chunks`

保存文档切分后的文本片段，用于聊天前的资料检索。

### `users`

保存登录账号、密码哈希、角色、启用状态和创建时间。默认启动时会创建管理员账号。

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

当前 DeepSeek、豆包、百炼都走 `LangChain + ChatOpenAI(OpenAI-compatible)` 方案；本地演示模型使用自定义运行时。若某个厂商接口协议不同，可以新增独立 LangChain runtime，而不需要改动路由层。

视觉问答通过 `ChatRequest.attachments` 传入图片附件。后端在 LangChain 运行时层把附件转为 `image_url` content part，格式兼容 OpenAI-style 多模态接口。纯文本模型会在路由层被拦截，避免把图片误发给不支持视觉的模型。

## 场景化策略

当前项目默认包装成“校园服务智能助手”，场景层会在模型调用前追加系统提示词，让模型优先按办事咨询、宿舍报修、图片故障描述、天气出行等服务台任务组织回答。

前端通过 `/api/options` 获取场景标题、说明和快捷问题。后续切换到“企业 IT 服务台”“政务办事助手”“医院导诊助手”等场景时，优先修改 `app/scenario.py`，再按业务需要扩展工具、知识库或表单字段。

## 知识库策略

当前知识库采用轻量混合 RAG：

- 上传资料后保存到 `data/knowledge/`。
- 文本被切成重叠片段并存入 SQLite。
- 用户提问时，后端按关键词重叠、文件名/标题匹配和字符 n-gram 相似度综合排序。
- 命中的片段会作为“知识库”工具结果注入模型上下文。

这种方案部署简单，适合演示和小规模资料。资料量变大后，可以把 `app/knowledge.py` 中的 `search_knowledge()` 替换为向量检索、BM25 或全文检索。

## 鉴权策略

当前项目使用本地用户表和签名 Bearer Token：

- 密码通过 PBKDF2 哈希后存储，不保存明文密码。
- Token 使用服务端密钥签名，并带过期时间。
- 普通业务接口和知识库上传需要登录用户。
- 审计接口要求 `admin` 角色。

该方案适合本地部署、课程设计和小型内部系统演示。生产系统可进一步替换为 JWT 标准库、OAuth2/OIDC、RBAC 权限表和用户维度的数据隔离。

## 工具调用策略

后端在调用模型前会根据用户问题自动选择工具：

- 当前时间：本地工具，无需网络或 API Key
- 实时天气：通过 `wttr.in` 查询，适合演示用途
- 联网搜索：优先 Tavily，其次 SerpAPI

工具结果会先通过 LangChain tools 执行，再写入一条临时 `system` 消息交给模型回答。这样即使厂商接口暂未启用原生 function calling，也能让模型利用实时信息。流式接口会额外返回 `tool` 事件，前端统计栏会显示本次调用了哪些工具。

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

- 增加 WebSocket 双向语音会话
- 将 provider 配置改为数据库或管理后台动态配置
- 将轻量混合知识库升级为向量库，支持更大规模资料和语义检索
- 增加用户维度的数据隔离，让不同用户只看到自己的会话和资料
