# LLM API Router 部署文档

## 项目简介

LLM API Router 是一个支持多厂商大模型统一接入、用户私有路由管理、调用日志统计的网关服务，支持OpenAI兼容接口协议。

***

## 环境要求

- 服务器：Linux (Ubuntu 20.04+/Debian 11+/CentOS 8+)
- 运行环境：Python 3.8 \~ 3.12
- 最低配置：1核2G（推荐2核4G，根据并发量调整）
- 端口要求：需要开放服务端口（默认8000）

***

## 部署方式（优先推荐原生Systemd部署，兼容国内复杂网络环境）

### 方式一：Systemd 原生部署（推荐，100%兼容所有服务器）

#### 1. 克隆代码到服务器

```bash
git clone <你的仓库地址> /opt/llm-api-router
cd /opt/llm-api-router
```

#### 2. 配置核心参数（必须修改）

```bash
# JWT 签名密钥：必须通过环境变量设置（生产环境禁止使用默认内置密钥）
# 生成随机密钥示例：
#   openssl rand -hex 32
# 在 systemd、shell 或 .env 中设置其一即可：
#   export LLM_ROUTER_JWT_SECRET="$(openssl rand -hex 32)"
# 兼容别名：若未设置 LLM_ROUTER_JWT_SECRET，可设置 SECRET_KEY。
# Token 有效期（天，可选，默认 7）：
#   export LLM_ROUTER_ACCESS_TOKEN_EXPIRE_DAYS=30

# 配置全局服务商（可选，所有用户共用的大模型配置）
vim config.yaml
```

#### 3. 安装依赖

```bash
# 安装Python虚拟环境
sudo apt update && sudo apt install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate

# 用国内清华源安装依赖，速度更快
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 修复bcrypt兼容性问题（必做）
pip install bcrypt==3.2.2 -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 4. 配置Systemd守护进程

**切勿**把下面示例里的 `Environment=LLM_ROUTER_JWT_SECRET=请替换为...` **原样保留**：若 systemd 以该字面量作为密钥，网关虽能启动，但属于弱且固定的密钥。请二选一：

1. **推荐**：创建 `/etc/llm-api-router.env`（`chmod 600`），内容为一行真实密钥，例如 `LLM_ROUTER_JWT_SECRET=` + `openssl rand -hex 32` 的输出；在 unit 中使用 `EnvironmentFile=-/etc/llm-api-router.env`，并**删除**示例中的 `Environment=LLM_ROUTER_JWT_SECRET=...` 整行。  
2. **或**：将 `Environment=` 一行中的占位整段替换为 `LLM_ROUTER_JWT_SECRET=<你的随机串>`（长度须 ≥ 16，与程序校验一致）。

```bash
sudo tee /etc/systemd/system/llm-api-router.service <<-'EOF'
[Unit]
Description=LLM API Router Service
After=network.target

[Service]
WorkingDirectory=/opt/llm-api-router
# 推荐：/etc/llm-api-router.env 内写 LLM_ROUTER_JWT_SECRET=真实随机串，然后启用下一行并删除下一行的 Environment=
# EnvironmentFile=-/etc/llm-api-router.env
Environment=LLM_ROUTER_JWT_SECRET=请替换为openssl_rand_hex32的输出
ExecStart=/opt/llm-api-router/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

#### 5. 启动服务并设置开机自启

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now llm-api-router
```

#### 6. 验证部署成功

```bash
# 查看服务状态，看到active(running)即为成功
sudo systemctl status llm-api-router

# 确认 unit 已加载环境（勿在公共日志中打印含密钥的完整输出）
sudo systemctl show llm-api-router -p Environment --no-pager

# 测试健康检查接口
curl http://localhost:8000/health
# 返回 {"status":"ok"} 说明服务正常
```

***

### 方式二：Docker 部署（适合网络环境良好的服务器）

#### 1. 克隆代码并修改配置

```bash
git clone <你的仓库地址> /opt/llm-api-router
cd /opt/llm-api-router
# 设置环境变量 LLM_ROUTER_JWT_SECRET（及可选的 config.yaml），同上节「配置核心参数」
```

#### 2. 安装Docker和Docker Compose

```bash
# 官方一键安装脚本
curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh
sudo systemctl enable docker --now
```

#### 3. 启动服务

```bash
# 创建数据目录，持久化数据库
mkdir -p data
sudo chmod 755 data/

# 启动服务
sudo docker compose up -d --build
```

#### 4. 验证部署

```bash
sudo docker compose ps
# 看到STATUS为Up即为成功
curl http://localhost:8000/health
```

***

## 公网访问配置

### 1. 云服务商安全组放行

登录云服务商控制台，在安全组添加入方向规则：

- 端口：TCP 8000
- 源地址：0.0.0.0/0（允许所有IP访问）

### 2. 服务器防火墙放行

```bash
# Ubuntu/Debian
sudo ufw allow 8000/tcp
sudo ufw reload

# CentOS/RHEL
sudo firewall-cmd --add-port=8000/tcp --permanent
sudo firewall-cmd --reload
```

### 3. 访问后台

打开浏览器访问：`http://你的公网IP:8000/admin`，注册账号即可使用。

***

## 常见问题排查

### 1. Docker镜像拉取失败

```bash
# 配置国内Docker镜像源
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.aliyun.com",
    "https://hub-mirror.c.163.com"
  ]
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
```

### 2. bcrypt兼容性报错 `AttributeError: module 'bcrypt' has no attribute '__about__'`

```bash
source venv/bin/activate
pip install bcrypt==3.2.2
sudo systemctl restart llm-api-router
```

### 3. 公网无法访问服务

- 确认服务监听地址是`0.0.0.0`不是`127.0.0.1`
- 确认云服务商安全组已经放行对应端口
- 确认服务器内部防火墙已经放行端口
- 确认端口没有被其他进程占用：`ss -tunlp | grep 8002`

### 4. APT/PIP安装依赖失败

```bash
# 配置国内APT源
sudo tee /etc/apt/sources.list <<-'EOF'
deb http://mirrors.aliyun.com/ubuntu/ noble main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ noble-security main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ noble-updates main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ noble-backports main restricted universe multiverse
EOF
sudo apt update

# PIP临时使用国内源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

***

## 运维管理命令

### Systemd 部署

```bash
# 查看服务状态
sudo systemctl status llm-api-router

# 重启服务（更新代码/修改配置后执行）
sudo systemctl restart llm-api-router

# 停止服务
sudo systemctl stop llm-api-router

# 查看实时运行日志
sudo journalctl -u llm-api-router -f

# 查看最近100行日志
sudo journalctl -u llm-api-router -n 100 --reverse
```

### Docker 部署

```bash
# 查看服务状态
sudo docker compose ps

# 重启服务
sudo docker compose restart

# 查看日志
sudo docker compose logs -f

# 停止服务
sudo docker compose down

# 更新代码后重新构建
git pull
sudo docker compose up -d --build
```

***

## 数据备份

所有用户数据、路由配置、调用日志都存储在：

- Systemd部署：`/opt/llm-api-router/data/logs.db`
- Docker部署：`/opt/llm-api-router/data/logs.db`

定期备份该文件即可，恢复时直接替换文件重启服务即可。
