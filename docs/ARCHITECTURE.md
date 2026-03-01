# CowAgent 改造架构说明（Phase 0 勘探 + 改造点）

## 当前仓库数据流（勘探结论）

1. 入口在 `app.py` 通过 `ChannelManager` 启动多通道（企微、公众号、web 等）。
2. 通道统一进入 `channel/chat_channel.py`：
   - `_compose_context` 负责会话 ID、前缀触发、群聊/单聊路由。
   - `_handle` -> `_generate_reply` -> `_send_reply` 完成消息闭环。
3. 图片消息在 `ContextType.IMAGE` 分支仅缓存到 `memory.USER_IMAGE_CACHE`，没有业务化持久层。
4. 现有 Agent 内存位于 `agent/memory/*`，使用 SQLite（`sqlite3`）存 conversation/memory 索引。
5. 目前没有独立招聘业务数据库、没有统一指标 API、没有招聘看板后端。

## 改造点清单

### A. 数据边界
- 新增 `mcp_api_server` 独立服务：
  - PostgreSQL + SQLAlchemy + Alembic。
  - 统一业务 API（候选人/消息/媒体/事件/指标/Prompt/导出）。
- 招聘业务数据不再落 SQLite。
- 图片文件仅存文件系统（`MEDIA_DIR`），PostgreSQL 仅存 metadata/path。

### B. Agent Runtime
- 在 `channel/chat_channel.py` 消息主链路增加 MCP 调用：
  - 每条消息写入 `/messages`。
  - 触发事件写 `/events`。
  - 图片消息上传 `/media/upload` 并固定回复审核话术。
- 增加未成年终止、拒绝发照计数策略函数（可测试）。
- 模型系统提示词从 MCP `/prompts/current` 拉取（带缓存）。

### C. Dashboard
- 新增 `dashboard`（Next.js App Router）。
- 所有页面只走 MCP API，不直连 DB。
- 包含登录、概览、候选人列表/详情、Prompt 管理。

### D. 部署
- `docker-compose.yml` 统一拉起：postgres + mcp-api-server + agent-runtime + dashboard + nginx。
- 首次启动自动 Alembic migrate + 初始化管理员 + Prompt v1。
