# ouzi-AI

一个偏后端的 Python 多模态 AI 对话平台原型，支持：

- 多模型选择：DeepSeek、豆包、百炼，以及本地演示模型
- AI 对话：统一 provider 适配层
- 流式输出：`/api/chat/stream` 使用 SSE 实时返回 token
- 用户会话与聊天历史：SQLite 持久化保存
- 模型调用审计：记录模型、provider、token、费用、耗时和状态
- 语音识别 ASR：统一接口，当前提供演示实现
- 语音合成 TTS：统一接口，当前提供浏览器语音播放与演示音频接口
- 前端展示：聊天、模型/语音引擎选择、麦克风录音、音频波形可视化
- 图片上传与视觉问答：上传图片后自动切换到视觉模型，并将图片随对话发送

## 快速启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

打开：

```text
http://127.0.0.1:8001
```

## 项目结构

```text
python-ai小助手/
├── README.md                         # 项目说明、启动方式、配置说明
├── requirements.txt                  # Python 依赖
├── .gitignore                        # 忽略 .env、数据库、缓存等本地文件
│
├── app/                              # Python 后端
│   ├── __init__.py
│   ├── main.py                       # FastAPI 入口、路由、SSE 流式输出
│   ├── config.py                     # .env / 环境变量配置
│   ├── schemas.py                    # Pydantic 请求/响应模型
│   ├── providers.py                  # 多模型适配：DeepSeek、豆包、百炼、本地演示
│   ├── tools.py                      # 外部工具：当前时间、天气、联网搜索
│   ├── speech.py                     # 语音服务：浏览器、火山预留、百度 ASR/TTS
│   ├── uploads.py                    # 图片上传、校验、data URL 转换
│   ├── database.py                   # SQLite 会话、聊天历史、审计记录
│   └── metrics.py                    # token 估算与费用统计
│
├── static/                           # 前端页面
│   ├── index.html                    # 页面结构
│   ├── styles.css                    # 页面样式
│   └── app.js                        # 聊天、流式输出、语音播放、会话切换
│
├── docs/
│   └── architecture.md               # 架构设计说明
│
└── data/                             # 运行时自动生成，不提交 Git
    ├── aethervoice.db                # SQLite 数据库
    ├── audio/                        # 百度 TTS 生成的 MP3 文件
    └── uploads/                      # 用户上传的图片
```

## Docker 一键部署

构建并启动：

```powershell
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8001
```

停止：

```powershell
docker compose down
```

说明：

- 容器内服务监听 `8000`
- 宿主机映射到 `8001`
- `./data` 会挂载到容器中，保留数据库、上传图片和生成音频
- `env_file: .env` 会把本地密钥注入容器

## 可选环境变量

真实模型调用需要在环境变量中配置密钥：

```text
DEEPSEEK_API_KEY=...
DOUBAO_API_KEY=...
DOUBAO_MODEL_ID=...
BAILIAN_API_KEY=...
ENABLE_TOOLS=true
TAVILY_API_KEY=...
SERPAPI_API_KEY=...

BAIDU_API_KEY=...
BAIDU_SECRET_KEY=...
BAIDU_CUID=aethervoice
BAIDU_ASR_RATE=16000
BAIDU_ASR_DEV_PID=1537
BAIDU_TTS_PER=0
BAIDU_TTS_SPD=5
BAIDU_TTS_PIT=5
BAIDU_TTS_VOL=5
```

未配置密钥时，平台会自动使用本地演示响应，方便先跑通界面和接口。

`TAVILY_API_KEY` 和 `SERPAPI_API_KEY` 二选一即可，用于联网搜索。未配置时仍然可以使用本地时间工具回答“今天几号”“现在几点”等问题。

豆包/火山方舟需要额外配置 `DOUBAO_MODEL_ID`。这里要填控制台里的“推理接入点 ID”，通常类似：

```env
DOUBAO_MODEL_ID=ep-xxxxxxxxxxxxxxxx
```

不要填展示名称 `豆包 Pro 32K` 或示例里的 `doubao-pro-32k`，否则火山方舟会返回 `404 Not Found`。

百度语音服务需要在百度智能云控制台创建语音技术应用，复制应用里的 `API Key` 和 `Secret Key`，分别填到 `BAIDU_API_KEY` 和 `BAIDU_SECRET_KEY`。配置后前端下拉框可选择：

- `百度智能云 ASR`：上传音频到后端后调用百度短语音识别。
- `百度智能云 TTS`：后端调用百度短文本合成，生成 MP3 并返回给前端播放。

常用百度语音参数：

- `BAIDU_ASR_DEV_PID=1537`：普通话输入法模型。
- `BAIDU_ASR_RATE=16000`：16k 采样率。
- `BAIDU_TTS_PER=0`：默认女声。
- `BAIDU_TTS_SPD/PIT/VOL`：语速、音调、音量，范围通常为 0-15。

## 视觉问答

前端点击回形针按钮可上传 PNG、JPEG、WebP、GIF 图片。后端会：

1. 校验图片类型和大小。
2. 保存到 `data/uploads/`。
3. 通过 `/media/uploads/...` 返回预览地址。
4. 调用视觉模型时将图片转成 `data:image/...;base64,...`，按 OpenAI-compatible 多模态消息格式发送给模型。

当前提供两个视觉模型入口：

- `百炼 Qwen VL Plus`：真实视觉问答模型，需要配置 `BAILIAN_API_KEY`。
- `本地视觉演示模型`：无需密钥，用于验证上传和前后端链路。

如果上传图片时当前选择的是纯文本模型，前端会自动切换到第一个支持视觉的模型。

## 数据存储

首次启动会自动创建 SQLite 数据库：

```text
data/aethervoice.db
```

包含：

- `chat_sessions`：用户会话
- `chat_messages`：聊天历史
- `model_call_audits`：模型调用审计、token 和费用统计
