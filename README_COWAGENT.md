# CowAgent（Welike 主播招募客服）工程化版本

## 1. 系统架构

```
CowAgent Runtime(chatgpt-on-wechat)
  -> MCP API Server(FastAPI)
    -> PostgreSQL
  -> Dashboard(Next.js)
```

- Agent 保留原仓库多通道能力（含 WeCom/企微接入）。
- 招聘业务数据通过 MCP API 服务统一管理。
- Dashboard 仅调用 MCP API，不直连数据库。

## 2. Phase 0 勘探结果
见 `docs/ARCHITECTURE.md`。

## 3. PostgreSQL 表结构（ERD 文本版）
- `candidates`（候选人主表）
- `conversations`（会话）
- `messages`（消息）
- `media_assets`（照片元数据）
- `events`（关键业务事件）
- `metrics_daily`（每日指标聚合）
- `prompts`（Prompt 版本）
- `users`（Dashboard 用户）

主索引/约束：
- `candidates.external_id` 唯一
- `conversations.session_key` 唯一
- `messages(conversation_id, created_at)` 复合索引
- `events(candidate_id, created_at)` 复合索引
- `metrics_daily.metric_date` 唯一

## 4. 一键启动

```bash
cp .env.example .env
docker compose up -d --build
```

启动后：
- MCP API: `http://localhost:8001`
- Dashboard: `http://localhost:3000`
- Nginx 统一入口: `http://localhost:8080`
- OpenAPI: `http://localhost:8001/docs`

默认管理员（可通过 env 覆盖）：
- 用户名 `admin`
- 密码 `admin123`

## 5. 关键 API（MCP Server）

### 鉴权
```bash
curl -X POST http://localhost:8001/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

### 候选人 upsert
```bash
curl -X POST http://localhost:8001/candidates/upsert \
  -H 'Content-Type: application/json' \
  -d '{"external_id":"wecom_u_001","nickname":"小雨","city":"上海"}'
```

### 追加消息
```bash
curl -X POST http://localhost:8001/messages \
  -H 'Content-Type: application/json' \
  -d '{"external_id":"wecom_u_001","session_key":"wecom_u_001","channel":"wecom","sender":"user","message_type":"text","content":"你好"}'
```

### 上传照片
```bash
curl -X POST http://localhost:8001/media/upload \
  -F external_id=wecom_u_001 \
  -F session_key=wecom_u_001 \
  -F channel=wecom \
  -F file=@./test.jpg
```

### 指标概览
```bash
curl http://localhost:8001/metrics/overview -H "Authorization: Bearer <TOKEN>"
```

### Prompt 发布
```bash
curl -X POST http://localhost:8001/prompts/publish \
  -H "Authorization: Bearer <TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"version":"v2","content":"...","published_by":"admin"}'
```

## 6. 业务规则落地

- 收到图片后固定回复：
  - `收到啦宝子～我这边提交审核，1–3 天审核出结果通知你～`
- 未成年：终止流程并写 `underage_stop` 事件。
- 拒绝发照：累计事件 `photo_refused_n`，超过两次停止施压。
- Prompt 默认版本：`prompts/recruiter_v1.md`，初始化入库 `v1` 并激活。

## 7. 配置项
- `POSTGRES_URL`
- `MCP_BASE_URL`
- `MEDIA_DIR`
- `MEDIA_TOKEN`
- `JWT_SECRET`
- `PROMPT_CACHE_SECONDS`
- `CORS_ALLOW_ORIGINS`

## 8. 健康检查
- MCP: `GET /health`
