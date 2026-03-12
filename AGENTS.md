# AGENTS.md — chatgpt-on-wechat / CowAgent 协作规范

在修改本仓库前请完整阅读本文件。该文档面向后续 AI agents，目标是：**在不破坏原有多通道能力的前提下，稳定迭代 CowAgent 招募场景**。

---

## 1. 项目目标与架构边界

本仓库目前同时包含两类能力：

1. **原生 chat/runtime 能力**（多通道、插件、技能、记忆、web console）
2. **CowAgent 招募业务能力**（MCP API + Postgres + 招募流程规则 + 招募看板）

当前推荐的数据流：

```text
Channel(企微/网页等) -> ChatChannel -> MCP API Server -> PostgreSQL
                                       -> 媒体文件存储(MEDIA_DIR)
Web Console Recruit 视图 / 独立 Dashboard -> MCP API Server
```

### 强约束
- 招募业务数据（候选人、消息、事件、媒体索引、prompt 版本、指标）应优先经 `mcp_api_server`。
- 不要把招募业务新逻辑散落在多个 channel 中，优先放在：
  - `common/cowagent_runtime.py`
  - `mcp_api_server/app/*`
  - `channel/web/*`（仅 UI/代理层）

---

## 2. 关键目录（仅列高频）

| 路径 | 用途 |
|---|---|
| `app.py` | 程序入口，多通道启动管理。 |
| `channel/` | 各通道实现；`chat_channel.py` 为核心公共消息处理链。 |
| `channel/web/` | 原生 Web Console（现已集成 Recruit 视图）。 |
| `models/` | 模型与 session 管理（如 `chatgpt`）。 |
| `common/cowagent_runtime.py` | Runtime -> MCP 调用封装与招募策略函数。 |
| `mcp_api_server/` | FastAPI MCP 服务，业务 API 与数据访问边界。 |
| `mcp_api_server/alembic/` | Postgres 迁移脚本。 |
| `dashboard/` | 独立 Next.js 看板（可选入口）。 |
| `prompts/recruiter_v1.md` | 默认“北北”Prompt v1 文本。 |
| `prompts/candidate_profile_extractor.md` | 静默会话资料抽取 Prompt，要求返回 JSON。 |
| `docs/ARCHITECTURE.md` | 改造架构/数据流说明。 |
| `docs/DECISIONS.md` | 关键工程决策记录（必须持续追加）。 |
| `docker-compose.yml` | 本地一键部署编排。 |

---

## 3. 修改优先级与设计原则

### 3.1 稳定性优先
- 优先保证已有通道不崩溃（wechat/wecom/web）。
- 对 `chat_channel.py` 的改动必须“最小侵入”。

### 3.2 业务边界优先
- 新增招募后台功能时，优先改 MCP API 与前端，不要直接跨层直连数据库。
- Web Console / Next.js 都应通过 MCP API 获取业务数据。

### 3.3 合规优先（招募场景）
- 禁止引导露骨/裸露内容。
- 禁止索要身份证/银行卡/验证码。
- 未成年立即终止流程。
- 拒绝发照超过两次停止施压。

### 3.4 文档与可运维优先
- 每次重要设计变化都要追加 `docs/DECISIONS.md`。
- 影响部署/配置时同步更新 `README_COWAGENT.md` 与 `.env.example`。

---

## 4. 编码规范（针对本仓库）

### Python
- 遵循现有风格，尽量小函数、低耦合。
- import 不要加 try/catch 包裹（运行时依赖问题应显式暴露）。
- 新增 API 必须返回清晰 JSON（含 `status` 或明确字段）。
- 对外部请求（如 MCP 代理）必须设置 timeout。

### Web Console（`channel/web`）
- 新视图接入必须：
  1) `chat.html` 加侧边栏入口 + `view-*` 容器
  2) `console.js` 加 `VIEW_META` 路由与懒加载
  3) 后端 `web_channel.py` 增加 `/api/*` 处理器
- 避免重复绑定事件（使用 dataset 标记或初始化开关）。
- 招募看板当前已带自动刷新；若继续调整刷新逻辑，务必在离开 `recruit` 视图时停止 timer，避免后台重复请求。
- 招募状态展示统一在前端映射为中文业务语义：`pending_photo` => `未发送照片`，`pending_review` => `已发送照片`；不要直接把中文状态值写入数据库。

### MCP API（FastAPI）
- 新接口默认考虑鉴权与权限（admin / readonly）。
- 媒体访问必须受控（token 或签名方式）。
- 新字段变更必须配套 Alembic migration。
- 资料抽取属于 MCP 业务边界，统一放在 `mcp_api_server/app/main.py` + `mcp_api_server/app/profile_extraction.py`，不要把“从历史会话提取 nickname/city/status”的逻辑再塞回 channel。
- 候选人资料抽取要求 AI 返回严格 JSON，并在服务端做状态白名单映射与容错；即使模型输出中文状态，也必须先映射回内部 ASCII 枚举再落库。

---

## 5. 数据与迁移规则

- 业务主存储：PostgreSQL。
- 媒体文件：文件系统/对象存储；DB 只存 metadata/path。
- 任何 schema 改动流程：
  1) 改 `mcp_api_server/app/models.py`
  2) 新增/更新 `mcp_api_server/alembic/versions/*`
  3) 更新文档（至少 README 表结构说明）

禁止只改 ORM 不改 migration（或反之）。

---

## 6. 测试与验收要求（提交前最少）

至少执行：

```bash
python -m unittest tests/test_cowagent_runtime.py
python -m unittest tests/test_candidate_profile_extraction.py
python -m compileall mcp_api_server/app channel/web/web_channel.py common/cowagent_runtime.py
```

若改了前端（`channel/web/chat.html` / `console.js` / `dashboard/*`）：
- 尝试截图验证页面可访问；若环境服务未启动，记录失败原因。

若改了 compose / docker / env：
- 尝试 `docker compose config`；若环境缺少 docker 命令，记录限制。

---

## 7. 提交流程（必须）

每次任务完成后：
1. `git status` 自检，确保仅包含本次改动。
2. 提交 commit（信息清晰描述范围）。
3. 生成 PR 说明（包含：动机、改动点、测试结果、已知限制）。

---

## 8. 常见坑位清单

1. **CORS 问题**：Dashboard 直连 MCP 时需检查 `CORS_ALLOW_ORIGINS`。
2. **代理鉴权过期**：Web Console 代理 MCP 时 token 失效要自动重登。
3. **重复事件绑定**：多次进入视图会触发重复请求/重复提交。
4. **会话/候选人ID混淆**：`session_key` 与 `candidate_id` 概念不要混用。
5. **图片 URL 泄露**：日志中不要打印可直接访问的原图敏感链接。
6. **MCP_BASE_URL 部署场景**：宿主机直接运行 `python app.py` 时，`MCP_BASE_URL` 默认 `http://127.0.0.1:8001`；Docker 内 `agent-runtime` 使用 `http://mcp-api-server:8001`。宿主机跑 app 时若 MCP 不可达，会出现 `Name or service not known` 的 WARNING。
7. **招募看板图片显示**：图片消息需经 `upload_photo` 成功入库才有 `media_asset_id`；`log_message` 对 image/voice/video/file 存 `[图片]` 等标签，不存临时路径；Web Console 通过 `/api/recruit/media/{asset_id}` 代理访问 MCP 媒体，`console.js` 对 `message_type===image` 且 `media_asset_id` 存在时渲染 `<img>`。
8. **资料抽取触发条件**：当前静默资料抽取只针对企微渠道且状态为 `pending_photo` 的候选人；一旦状态进入 `pending_review`（已发送照片）及后续审核态，就不会继续触发。
9. **抽取逻辑回归风险**：`nickname` / `city` / `status` 的规则测试应优先放在纯工具模块，避免单测因为本地缺少 `fastapi` / `sqlalchemy` 而无法运行。

---

## 9. 修改后文档更新矩阵

| 触发场景 | 必须更新 |
|---|---|
| MCP 接口、鉴权、状态流变化 | `README_COWAGENT.md` + `docs/DECISIONS.md` |
| 数据表/字段/索引变化 | `README_COWAGENT.md` + Alembic 脚本 + `docs/DECISIONS.md` |
| Runtime 规则变化（未成年/拒绝/prompt策略） | `docs/DECISIONS.md` + 对应测试 |
| 静默资料抽取规则 / 状态映射变化 | `README_COWAGENT.md` + `docs/DECISIONS.md` + 对应测试 |
| Web Console 视图与交互变化 | `README_COWAGENT.md`（入口/用法） |
| 新增环境变量 | `.env.example` + `README_COWAGENT.md` |

---

## 10. 对后续 agents 的工作建议

- 先读：`docs/ARCHITECTURE.md`、`docs/DECISIONS.md`、本文件。
- 再改：优先后端边界（MCP）再改前端表现层。
- 每次改动保持小步提交，确保可回滚。

如与用户需求冲突，以用户需求优先；如与系统/开发者指令冲突，以系统/开发者指令优先。
