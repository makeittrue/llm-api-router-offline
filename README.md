# llm-api-router-offline

LLM API 统一路由网关，聚合多个运营商的大模型API，提供统一的OpenAI兼容接口，支持多用户私有路由管理，自动路由转发，并记录所有调用日志。

## 功能特性

✅ **多运营商统一接入**  
支持OpenAI、DeepSeek、Moonshot、智谱等所有OpenAI兼容的API服务商，只需配置即可接入。

✅ **多用户隔离系统**
支持用户注册、登录，JWT鉴权，用户之间的数据完全隔离，每个用户只能看到自己的路由和调用日志。

✅ **用户私有路由管理**
每个用户可以独立添加、编辑、删除自己的私有路由，优先级高于全局路由，用户可以配置自己的服务商API密钥，完全独立管理。

✅ **可视化管理后台**
提供Web管理页面，支持路由管理、调用日志查询、用量统计可视化，操作简单直观。

✅ **自动路由转发**  
用户调用时只需指定模型名，内部自动根据配置转发到对应的运营商，用户无需关心API来源。支持模型名映射，对外暴露的模型名可以和运营商的实际模型名不同。

✅ **完整调用日志**  
SQLite存储所有调用记录，包含调用时间、token使用量、耗时、请求内容、状态等信息，支持日志查询和用量统计。

✅ **100% OpenAI 兼容**  
完全兼容OpenAI API格式，用户可以直接使用OpenAI SDK调用，无需修改代码。

✅ **流式响应支持**  
支持流式输出，和OpenAI流式响应格式完全一致。

## 🚧 开发计划（待办）
1. Demo网站正在搭建中
2. 增加费用限制功能，支持分路由统计用量（需要配置计费规则）
3. 支持用量达到阈值通知功能（可配置飞书、企业微信、邮件等通知渠道）
4. 扩展更多应用场景支持

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置全局路由（可选）
编辑 `config.yaml`，添加全局运营商API Key和路由映射（所有用户都可以使用这些公共路由）：
```yaml
providers:
  - name: openai
    base_url: "https://api.openai.com"
    api_key: "OPENAI_API_KEY"  # 支持从环境变量读取
    api_type: "openai"

routes:
  - model: "gpt-4o"
    provider: "openai"
    provider_model: "gpt-4o"
```

### 3. 启动服务
```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. 访问管理后台
浏览器打开 http://localhost:8000/admin
- 注册账号并登录
- 在「我的路由」页面添加你自己的私有路由
- 可以在「调用日志」和「用量统计」页面查看调用记录和消费情况

### 5. 调用测试
使用OpenAI SDK调用，只需在Header中添加你的Token即可：
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1", 
    api_key="这里替换为你登录后获取的Token"
)
response = client.chat.completions.create(
    model="你自己配置的模型名或者全局模型名",
    messages=[{"role": "user", "content": "你好！"}]
)
print(response.choices[0].message.content)
```

非流式调用：
```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 你的Token" \
  -d '{
    "model": "模型名",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

流式调用：
```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 你的Token" \
  -d '{
    "model": "模型名",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

## Trae IDE 接入（自定义 OpenAI 兼容网关）

Trae 在较新版本支持「自定义模型 / OpenAI 兼容服务商」等能力时，可以把 **Base URL** 指向本项目，从而在 IDE 内使用你在网关里配置的任意上游模型（含私有路由）。不同版本菜单位置可能略有差异，常见入口包括：**头像 → AI 与模型 / 模型管理 → 添加模型**，或聊天区域 **模型选择器 → 添加模型**。

### 1. 在本项目中先完成的准备

1. 按上文启动网关，并打开管理后台 `http://<host>:<port>/admin`。
2. **注册 / 登录**，复制登录接口返回的 **`access_token`**（即下文中的「网关 Token」）。Trae 里填的 API Key 应与此 Token 一致；请求头等价于 `Authorization: Bearer <Token>`。
3. 在 **「我的路由」** 添加私有路由，或依赖管理员在 `config.yaml` 中配置的 **全局路由**。Trae 里选用的 **模型名** 必须与网关对外暴露的 **`model` 字段** 完全一致（区分大小写）。

### 2. Trae 侧配置对照

| Trae 中的配置项 | 应填写的内容 |
|-----------------|--------------|
| **Base URL** / **OpenAI API 地址** | `http://<网关主机>:<端口>/v1`。**必须包含路径 `/v1`**（与 OpenAI 官方 SDK 习惯一致；本项目的 Chat 与模型列表均在 `/v1` 下）。 |
| **API Key** | 上一步获得的 **网关 JWT Token**（不是上游 DeepSeek/OpenAI 等厂商的 Key；厂商 Key 只在管理后台「我的路由」里配置）。 |
| **模型名称** | 与 `GET /v1/models` 返回列表中某个模型的 **`id`** 完全一致。 |

若 Trae 单独要求填写 **Model ID** 与 **显示名称**，显示名称可随意；**Model ID** 仍须与网关中的对外模型名一致。

### 3. 连接与模型列表自检（推荐）

在 Trae 保存配置前，可在终端用同一 Token 验证网关与模型名：

```bash
curl -sS "http://localhost:8000/v1/models" \
  -H "Authorization: Bearer 你的网关Token" | head
```

返回 JSON 中含 `"data": [ { "id": "..." } , ... ]` 即表示鉴权与路由正常；Trae 里选择的模型名须为其中某个 **`id`**。

### 4. 常见问题

- **401 / 鉴权失败**：Trae 中填的 API Key 不是本系统登录后的 Token，或 Token 已过期，请重新登录管理后台或调用 `/login` 获取新 Token。
- **404 / 模型不存在**：Trae 中的模型名与 `config.yaml` 或「我的路由」里的 **`model` 不一致**，或该用户未配置该模型；用上一节 `curl` 核对 `id` 列表。
- **Trae 与网关不在同一台机器**：Base URL 中不要用 `localhost`（除非 Trae 进程与网关同机）；应填 **运行 uvicorn 的那台机器的内网 IP 或域名**。
- **仅 HTTPS 环境**：若 Trae 或公司策略要求 HTTPS，请在网关前加 **Nginx / Caddy** 等反向代理终止 TLS，再把 Trae 的 Base URL 指向 `https://.../v1`。
- **添加模型时的「连接检测」失败**：Trae 会向 Base URL 发起校验请求；请保证该 URL 在 Trae 所在网络可达，且 **`GET /v1/models` 在携带 Bearer Token 时可返回 200**。
- **Trae 界面无法修改 Base URL**：部分版本或渠道可能仍限制仅官方端点；可向 Trae 官方反馈需求，或使用支持自定义 Base URL 的扩展能力（以 Trae 插件市场说明为准）。本项目网关侧已提供标准 **`/v1/chat/completions`** 与 **`/v1/models`**，与 OpenAI 兼容客户端协议对齐。

### 5. 与本项目 Trae 相关行为说明

网关对 Trae 发送的 **system**、工具痕迹等先做内容清理（去标签、冗余等），再按 **`config.yaml` 中的 `context` 段**（见下文「上下文与长窗口模型」）对单条消息字符数、历史条数、`max_tokens` 做上限控制。默认情况下，若 Trae 请求的 **`model` 名称**（对外模型名）包含 `deepseek-v4`、`mimo-2.5` 等子串，会自动采用 **约 1M 上下文类** 的宽松上限；其它模型仍使用保守默认值，避免误连小上下文上游时把请求撑爆。若仍出现上游 400 等错误，可在管理后台 **「调用日志」** 中查看 `error_message`，并核对上游 **`provider_model`** 是否在厂商侧有效。

## 配置说明

### 全局配置
```yaml
server:
  host: "0.0.0.0"
  port: 8000

log:
  db_path: "logs.db"  # SQLite日志文件路径
```

### Provider配置（全局）
```yaml
providers:
  - name: 服务商名称（唯一标识）
    base_url: 服务商API根地址
    api_key: API Key（如果是环境变量名，会自动从环境读取）
    api_type: "openai" # 目前只支持OpenAI兼容类型
```

### 路由配置（全局）
```yaml
routes:
  - model: 对外暴露的模型名
    provider: 对应的服务商名称（和上面provider的name对应）
    provider_model: 服务商侧的实际模型名（可选，和model相同可以省略）
```

### 上下文与长窗口模型（可选 `context`）

用于控制网关对 **Trae 等客户端** 的裁剪强度与 `max_tokens` 兜底。若请求里的 **`model` 字段**（对外模型名，与路由里配置的 `model` 一致）匹配 `long_context_model_substrings` 中任一字串（不区分大小写），则使用 `long_context_*` 一组参数（默认可覆盖 **DeepSeek V4、MiMo 2.5** 等约 1M 上下文场景）；否则使用上方的保守默认值。

```yaml
context:
  message_char_cap: 2000
  history_message_keep: 10
  max_tokens_default: 4096
  max_tokens_cap: 8192
  long_context_model_substrings:
    - "deepseek-v4"
    - "mimo-2.5"
  long_context_message_char_cap: 800000
  long_context_history_message_keep: 256
  long_context_max_tokens_default: 32768
  long_context_max_tokens_cap: 393216
```

`message_char_cap` / `long_context_message_char_cap` 设为 **0** 表示不对单条消息做字符截断（仍会做标签清理）。完整字段说明以 `app/config.py` 中 `ContextConfig` 为准。

## 用户私有路由说明
每个用户可以在管理后台独立添加自己的私有路由：
- 私有路由优先级高于全局路由，如果用户的私有路由和全局路由模型名相同，优先使用用户自己的配置
- 用户可以配置任意兼容OpenAI格式的服务商API
- 用户的API密钥会加密存储在数据库中，只有用户自己可以使用
- 支持路由的增删改查操作，实时生效

## API 文档
所有API接口（除了注册和登录）都需要在请求头中添加`Authorization: Bearer <你的Token>`进行鉴权。

### 1. 用户注册
```
POST /register
```
请求体：
```json
{
    "username": "用户名",
    "password": "密码"
}
```
返回：
```json
{
    "user_id": 1,
    "username": "用户名",
    "access_token": "JWT Token",
    "token_type": "bearer"
}
```

### 2. 用户登录
```
POST /login
```
请求体：`application/x-www-form-urlencoded`格式，包含`username`和`password`字段。
返回和注册接口相同。

### 3. Chat Completions
```
POST /v1/chat/completions
```
完全兼容OpenAI Chat Completions API格式，需要鉴权。

### 4. 模型列表
```
GET /v1/models
```
返回所有可用模型（全局模型+当前用户的私有模型），需要鉴权。

### 5. 查询调用日志
```
GET /v1/logs?model=<模型名>&limit=<条数>&offset=<偏移>
```
返回当前用户的调用日志列表，需要鉴权。

### 6. 用量统计
```
GET /v1/logs/summary?model=<模型名>
```
返回当前用户的各模型调用次数、总token使用量、平均耗时等统计信息，需要鉴权。

### 7. 查询用户私有路由列表
```
GET /v1/user/routes
```
返回当前用户的所有私有路由配置，需要鉴权。

### 8. 添加私有路由
```
POST /v1/user/routes
```
请求体：
```json
{
    "model": "对外模型名",
    "provider_name": "服务商名称",
    "provider_base_url": "服务商API地址",
    "provider_api_key": "服务商API密钥",
    "provider_model": "服务商实际模型名",
    "provider_api_type": "openai"
}
```
需要鉴权。

### 9. 修改私有路由
```
PUT /v1/user/routes/{路由ID}
```
请求体格式和添加路由相同，只传需要修改的字段即可，需要鉴权。

### 10. 删除私有路由
```
DELETE /v1/user/routes/{路由ID}
```
需要鉴权。

### 11. 健康检查
```
GET /health
```
返回服务状态，不需要鉴权。

## 管理后台功能
访问地址：http://localhost:8000/admin
- **我的路由**：管理用户私有路由，支持增删改查
- **全局服务商**：查看系统配置的公共服务商列表
- **调用日志**：查看当前用户的所有调用记录，支持按模型筛选
- **用量统计**：可视化展示各模型调用次数和Token消耗情况，以及详细统计表格

## 日志字段说明
日志存储在SQLite的`call_logs`表中，包含以下字段：
| 字段 | 说明 |
|------|------|
| `request_id` | 请求ID |
| `model` | 调用的模型名 |
| `provider` | 实际转发的服务商 |
| `provider_model` | 服务商侧的模型名 |
| `prompt_tokens` | 输入token数 |
| `completion_tokens` | 输出token数 |
| `total_tokens` | 总token数 |
| `is_stream` | 是否流式调用 |
| `status` | 调用状态（success/error） |
| `error_message` | 错误信息 |
| `user_id` | 用户ID |
| `request_messages` | 请求消息JSON |
| `created_at` | 调用时间 |
| `duration_ms` | 调用耗时（毫秒） |

## 扩展新服务商
### 全局服务商
只需在`config.yaml`中添加新的provider和对应的route即可，无需修改代码，所有兼容OpenAI API格式的服务商都可以直接接入。

### 用户私有服务商
用户直接在管理后台的「我的路由」页面添加即可，无需管理员操作。

## 项目结构
```
├── app/
│   ├── main.py           # FastAPI主应用
│   ├── config.py         # 配置加载
│   ├── models.py         # 数据模型
│   ├── router.py         # 路由调度
│   ├── logger.py         # 日志存储 + 数据库操作
│   ├── utils.py          # 工具函数（JWT、密码哈希）
│   └── providers/
│       └── base.py       # Provider适配层
├── static/
│   └── admin.html        # 管理后台页面
├── config.yaml           # 全局配置文件
├── requirements.txt
├── .gitignore
└── README.md
```
