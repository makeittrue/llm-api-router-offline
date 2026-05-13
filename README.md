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

## 开发路线图（落地顺序）

以下按**推荐实施顺序**排列：先做工程卫生与密钥治理，再计费配额与上下文能力，随后韧性/审计/规模化与测试，最后 Demo 与产品叙事（P9/P1 也可在 P7 之后并行推进）。条目可直接作为 issue 拆分；前缀 **Pxx** 便于在 PR 标题中引用。

### P10 — 工程卫生（优先）

- [x] `app/utils.py`：JWT 密钥与 `ACCESS_TOKEN_EXPIRE_DAYS` 从环境变量读取；缺省时在文档中标明仅限开发；更新 `DEPLOY.md` / 本文「快速开始」中的必填项说明
- [ ] 引入标准 `logging`（可选 uvicorn/logger 配置），替换 `app/main.py`、`app/providers/base.py` 中对请求与 payload 的 `print`
- [ ] 删除 `app/main.py` 中重复注册的 `GET /health`，保留单一实现并核对 OpenAPI
- [ ] （可选）新增 `.env.example`：仅列出变量名与说明，不包含真实密钥

### P4 — 安全：用户路由 API Key

- [ ] 选定应用级加密方案（如 Fernet + 环境变量 `ROUTER_DATA_KEY`），文档说明密钥轮换与备份要求
- [ ] `user_routes.provider_api_key`：写入前加密、读出解密；兼容存量明文的一次性迁移（启动时或独立迁移脚本）
- [ ] 管理后台与 API：列表/表单仅展示脱敏 Key（如 `sk-***abcd`），不向浏览器返回完整明文
- [ ] README：与「加密存储」实际行为一致；补充数据密钥丢失后的不可恢复说明

### P5 — 权限：管理员与普通用户

- [ ] 数据模型：`users` 表增加 `role`（admin/user）或 `is_admin`；迁移策略：首个注册用户为 admin，或由环境变量指定管理员用户名
- [ ] FastAPI：`require_admin` 依赖，校验 JWT 与库中角色一致
- [ ] `GET /v1/admin/routes`、`GET /v1/admin/providers` 仅管理员可访问，普通用户返回 403
- [ ] `static/admin.html`：非管理员隐藏「全局服务商/路由」或只读提示，与 API 行为一致

### P2 — 计费 MVP

- [ ] 计费配置：在 `config.yaml` 或数据库中定义模型/路由维度的单价（如每 1K input/output token）及生效时间
- [ ] 聚合查询：基于 `call_logs` 按日/周/月汇总 token 与估算费用（SQLite SQL 或后台任务）
- [ ] API：如 `GET /v1/billing/summary`（路径可调整）返回周期用量与费用，与用户隔离
- [ ] 管理后台：用量统计增加费用列或独立「账单」页；可选 CSV/JSON 导出

### P3 — 配额与告警

- [ ] 配额模型：用户级或「用户 + 模型」级的日度/月度 token 或调用次数上限（存 DB 或配置）
- [ ] 请求前置：在 `chat_completions` 转发前校验配额；超额返回 OpenAI 风格错误与可读文案
- [ ] 通知抽象：定义 `Notifier` 接口（如 webhook）；占位实现 + 飞书或企业微信之一的最小可用适配器
- [ ] 阈值：配额达到 80% / 100% 时触发通知（同步计数或定时扫描 `call_logs`）

### P7 — 上下文与 Trae 相关（`ContextConfig` 落地）

- [ ] 实现请求预处理模块：读取 `AppConfig.context`（消息裁剪、合并连续 assistant、synthetic user、路径前缀替换等）
- [ ] 在流式/非流式 `chat_completions` 调用上游前接入预处理；按 `route_targets_long_context` 选用正确上限
- [ ] 按需接入 `tiktoken` 做 token 级裁剪；若仅字符级裁剪，须在文档中明确说明
- [ ] README 与 `config.yaml` 示例与真实行为对齐；若有调试/关闭裁剪开关则写入文档

### P8 — 路由韧性

- [ ] httpx 层：对 429 / 502 / 503 可配置重试与指数退避（区分流式与非流式）
- [ ] 限流：按 `user_id` 或 IP 的滑动窗口（如 slowapi）；配置项放入 `config.yaml`
- [ ] （可选）同一对外 `model` 配置主备上游，失败自动切换并打日志标签

### P6 — 审计与可观测

- [ ] 日志查询：支持按 `status`、时间范围、错误子串筛选；必要时增加索引（如 `user_id` + `status` + `created_at`）
- [ ] 管理端：错误信息聚合（按 `error_message` 归一统计 Top N）接口或页面
- [ ] （可选）操作审计表：登录、改路由、改配额等管理类写操作

### P11 — 规模化：超越单机 SQLite

- [ ] 文档：多实例部署下 SQLite 的局限与迁移检查清单（锁、备份等）
- [ ] 抽象数据访问层；优先实现 PostgreSQL（或 MySQL）方言的日志与用户路由存储（可引入 SQLAlchemy 最小子集）

### P12 — 自动化测试

- [ ] 引入 pytest + httpx ASGI Client；fixtures 使用内存库或临时 SQLite
- [ ] Mock 上游：覆盖私有路由优先、全局路由、404 model、流式首包错误、非流式 `UpstreamError`
- [ ] 覆盖管理员鉴权、配额耗尽、（若已实现）请求预处理边界

### P9 — Demo 与分发

- [ ] `docker-compose`：增加 `demo` profile 或独立 compose（资源限制、只读演示账号说明）
- [ ] 提供 Nginx / Caddy HTTPS 反代示例与「首次 curl 自检」文档段落

### P1 — 产品叙事与文案

- [ ] 选定产品主轴（IDE/Trae、企业内部网关、个人聚合等）后重写 README 开篇：一句话价值、典型用户、明确非目标边界
- [ ] 欢迎页 `/` 与管理后台文案与主轴一致；将本路线图中的已完成项勾选或链接到 milestone/issue

---

**历史简述（已并入上文各阶段）**：Demo 站点、分路由计费与用量、阈值通知（飞书/企微/邮件）、更多应用场景，均体现在 P9、P2、P3 与 P1 中。

## 快速开始

### 环境变量（JWT）

| 变量名 | 生产环境 | 说明 |
|--------|----------|------|
| `LLM_ROUTER_JWT_SECRET` | **必填** | 用于签发与校验用户 JWT 的 HMAC 密钥；**长度须 ≥ 16**（代码校验），生产建议使用 `openssl rand -hex 32`。 |
| `SECRET_KEY` | 二选一 | 仅当**未设置** `LLM_ROUTER_JWT_SECRET` 时作为回退；**若两者同时存在，始终以 `LLM_ROUTER_JWT_SECRET` 为准**。 |
| `LLM_ROUTER_ACCESS_TOKEN_EXPIRE_DAYS` | 可选 | 访问令牌有效天数，正整数，**默认 `7`**。 |

- 同一台机器上若已有其它应用使用通用名 `SECRET_KEY`（如 Django / Flask），为避免网关误读他人配置，**请为本项目单独设置 `LLM_ROUTER_JWT_SECRET`**，而不要依赖 `SECRET_KEY`。
- 若上述两个密钥变量均未设置，服务会使用**仅适用于本地开发**的内置默认密钥，并在导入 `app.utils` 时发出 `UserWarning`。**生产部署必须设置 `LLM_ROUTER_JWT_SECRET`（或足够长的 `SECRET_KEY`）。**

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

用于控制网关对 **Trae 等客户端** 的裁剪强度与 `max_tokens` 兜底。若请求里的 **`model` 字段**（Trae 里选的对外模型名）或该路由对应的 **`provider_model`（上游真实模型名）** 任一匹配 `long_context_model_substrings` 中任一字串（不区分大小写），则使用 `long_context_*` 一组参数（默认可覆盖 **DeepSeek V4、MiMo 2.5** 等约 1M 上下文场景）；否则使用上方的保守默认值。这样 Trae 里自定义显示名不含 `v4` 时，只要后台路由的上游模型名含 `deepseek-v4` 等，仍会走长上下文策略。

```yaml
context:
  message_char_cap: 2000
  history_message_keep: 10
  max_tokens_default: 4096
  max_tokens_cap: 8192
  long_context_model_substrings:
    - "deepseek-v4"
    - "deepseek v4"
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
返回当前用户的调用日志列表，需要鉴权。响应 JSON 含 `data`（日志数组）、`total`（符合条件的总条数）、`limit`、`offset`；`limit` 最大 100，默认 20。

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
