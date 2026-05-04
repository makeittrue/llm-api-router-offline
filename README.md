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
