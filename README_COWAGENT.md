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
- Dashboard: `http://localhost:3000`
- Nginx 统一入口: `http://localhost:8080`
- OpenAPI: `http://localhost:8001/docs`
- Web Console 招募看板默认不再每 15 秒自动刷新，需通过页面上的“立即刷新”按钮手动拉取最新数据，避免页面周期性卡顿。

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
- Prompt 默认版本：`prompts/recruiter_v1.md`。当 MCP 当前激活版本为 `v1` 时，`GET /prompts/current` 直接读取该文件作为主 system prompt，并在其末尾拼接所有已审核通过的训练语料。
- 招募看板状态展示使用中文业务语义：`pending_photo` => `未发送照片`，`pending_review` => `已发送照片`，`reviewing` => `审核中`，`passed` => `已通过`，`rejected` => `已拒绝`，`need_more_photo` => `需补照片`。

### 定时任务（跟进 + 资料抽取）

- **两个自动化**：① **跟进**：每 `FOLLOWUP_INTERVAL_SECONDS`（默认 3600，即 1 小时）扫描静默超过 `FOLLOWUP_MIN_IDLE_HOURS`（默认 2 小时）的对话，由 AI 判断是否发送跟进消息；② **资料抽取**：每 `PROFILE_EXTRACTION_INTERVAL_SECONDS`（默认 300，即 5 分钟）扫描一次，对「静默满 `PROFILE_EXTRACTION_IDLE_MINUTES`（默认 20 分钟）」的会话发起一次独立 AI 抽取。
- 需至少开启其一调度器才会启动：设置 `FOLLOWUP_ENABLED` 或 `PROFILE_EXTRACTION_ENABLED` 为 `true`/`1`/`yes`。本地运行请确保项目根目录有 `.env` 或在环境中导出上述变量；Docker 下由 docker-compose 传入，可在 `.env` 中覆盖。

### 候选人资料自动抽取

- 企微候选人会在会话静默达到 `PROFILE_EXTRACTION_IDLE_MINUTES`（默认 20 分钟）后，由调度器在下次扫描时发起一次独立 AI 抽取。
- 抽取结果要求返回 JSON，并回写 `nickname`、`city`、`status` 到候选人资料。
- 只有 `pending_photo`（看板显示为 `未发送照片`）状态会继续触发这类静默抽取。
- 一旦进入 `pending_review`（看板显示为 `已发送照片`）及后续审核状态，就不再继续触发抽取。
- 每次抽取结果都会写入 `events` 表，事件类型为 `candidate_profile_extracted`，便于审计与避免重复处理。

## 7. 企微域名校验与 Nginx（kf.welikefun.cn）

- 企微客服域名需在根路径可访问验证文件：`https://kf.welikefun.cn/WW_verify_fw4kBfmxdeLF0r3z.txt`
- 本仓库 Web 通道已提供该路径（默认端口 `web_port` 为 9899）。宿主机 Nginx 配置：`/etc/nginx/sites-available/kf.welikefun.cn`，已包含对该 URL 的转发。

本地验证（应用已启动时）：
```bash
curl -s http://127.0.0.1:9899/WW_verify_fw4kBfmxdeLF0r3z.txt
# 应返回文件内容，例如：fw4kBfmxdeLF0r3z
```

## 8. 配置项
- `POSTGRES_URL`
- `MCP_BASE_URL`
- `MEDIA_DIR`
- `MEDIA_TOKEN`
- `JWT_SECRET`
- `PROMPT_CACHE_SECONDS`
- `CORS_ALLOW_ORIGINS`
- `PROFILE_EXTRACTION_ENABLED`
- `PROFILE_EXTRACTION_INTERVAL_SECONDS`
- `PROFILE_EXTRACTION_IDLE_MINUTES`
- `PROFILE_EXTRACTION_BATCH_LIMIT`
- `PROFILE_EXTRACTION_CHANNELS`
- `PROFILE_EXTRACTION_MODEL`
- `PROFILE_EXTRACTION_TEMPERATURE`
- `PROFILE_EXTRACTION_MAX_TOKENS`

## 9. 健康检查
- MCP: `GET /health`
