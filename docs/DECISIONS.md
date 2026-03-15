# 决策记录

## 2026-03-01

1. **业务图片不入 PostgreSQL 二进制**
   - 原因：降低 DB 体积与备份压力，便于后续接入对象存储。
   - 方案：文件落 `MEDIA_DIR`，`media_assets` 仅保存路径和元数据。

2. **MCP API 鉴权采用 JWT Bearer**
   - 原因：Dashboard 与 API 分离部署更易扩展。
   - 管理类接口（Prompt 发布/回滚）要求 admin 角色。

3. **Agent 与业务数据解耦**
   - 原因：保留原仓库多通道能力，避免深改底层 memory 子系统。
   - 方案：仅在 `chat_channel` 链路插入 MCP 调用，最小侵入。

4. **Prompt 版本化落库**
   - 原因：满足“北北”策略可追溯、可回滚、可灰度缓存。
   - 方案：`prompts` 表维护版本；runtime 30s 缓存。

5. **Dashboard 首版采用服务端+客户端混合渲染最小 MVP**
   - 原因：优先闭环可用，不引入复杂状态库。

6. **Dashboard 与 MCP 跨域调试问题修复**
   - 现象：浏览器从 `localhost:3000` 调 MCP `localhost:8001` 触发 CORS。
   - 方案：MCP Server 增加 `CORSMiddleware`，并通过 `CORS_ALLOW_ORIGINS` 配置来源。

7. **补充最小策略单元测试**
   - 方案：新增 `tests/test_cowagent_runtime.py` 验证未成年识别、拒绝识别、拒绝计数阈值。

## 2026-03-04

8. **训练语料（ICL Prompt Examples）系统**
   - 原因：运营需要快速纠正 AI 客服的不当回复，而无需重写整个 Prompt。
   - 方案：新增 `prompt_examples` 表，存储 (上下文摘要, 确定回复) 对。审核通过的条目在 `GET /prompts/current` 时自动追加到基础 prompt 末尾，作为 in-context learning 补充。
   - 数据流：Web Console → MCP API CRUD → DB；Runtime 通过已有的 `/prompts/current` 端点获取组装后的完整 prompt，无需改动 bot 层。
   - 迁移：`0002_prompt_examples`。

## 2026-03-06

9. **自动跟进调度系统（Auto Followup Scheduler）**
   - 原因：AI 客服只能被动回复，无法主动跟进沉默候选人，导致 pending_photo 对话容易"聊死"，降低发照转化率。
   - 方案：后台定时调度器（默认每 1 小时）扫描 `pending_photo` 状态的对话，用独立 AI 调用（不污染原会话上下文）评估是否需要跟进，若需要则生成跟进消息并通过原通道主动推送。
   - 过滤条件：最后消息发送者为 assistant、静默超过可配置阈值（默认 2h）、跟进次数未达上限（默认 3 次）。
   - 跟进次数通过 `events` 表 `auto_followup` 事件类型追踪，不新增列。
   - `conversations` 表新增 `last_active_at` 字段提升查询效率，`POST /messages` 时自动更新。
   - 跟进消息发送后同步追加到 Session 内存，保证候选人回复时对话连贯。
   - 新增配置项：`FOLLOWUP_ENABLED`、`FOLLOWUP_INTERVAL_SECONDS`、`FOLLOWUP_MIN_IDLE_HOURS`、`FOLLOWUP_MAX_ATTEMPTS`。
   - 迁移：`0003_conversations_last_active`。

10. **Session 过期后自动从 MCP 恢复对话历史**
    - 现象：企微用户间隔超过 `expires_in_seconds`（默认 3600s）未互动后，内存中 `ExpiredDict` 清除 session，AI 丢失上下文重复提问（姓名、城市等）。
    - 方案：在 `ChatGPTBot.reply()` 中检测 session 是否为刚重建（仅含 system prompt），若是则调用 `GET /conversations/history?session_key=xxx` 从 PostgreSQL 加载该会话的完整历史消息并注入 session，随后正常走 `discard_exceeding` 裁剪。
    - 效果：即使内存 session 过期，只要 MCP 有持久化记录，对话上下文就不会丢失。
    - 涉及文件：`mcp_api_server/app/main.py`（新端点）、`common/cowagent_runtime.py`（新方法）、`models/chatgpt/chat_gpt_bot.py`（恢复逻辑）。

## 2026-03-09

11. **候选人资料在 MCP 侧静默抽取**
    - 原因：`nickname` / `city` / `status` 之前主要依赖通道入口的即时字段，城市缺少稳定来源，导致招募看板经常出现空信息。
    - 方案：在 MCP API 内新增独立资料抽取逻辑，基于 `Conversation.last_active_at` 判断企微会话静默满 20 分钟后，读取该会话历史消息并调用独立 AI prompt 输出 JSON，回写候选人资料。
    - 边界：资料抽取不放回 channel 层，统一由 MCP 负责历史组装、JSON 校验、状态映射与落库，结果通过 `candidate_profile_extracted` 事件留痕。
    - 过滤：仅对 `pending_photo` 候选人触发；进入 `pending_review`（已发送照片）及后续审核状态后停止继续抽取。

12. **保留英文枚举，前端显示中文状态语义**
    - 原因：数据库和现有代码路径已依赖 `pending_photo` / `pending_review` 等 ASCII 状态值，直接改 Enum 会扩大迁移面并影响兼容性。
    - 方案：内部状态枚举保持不变，Web Console 与 MCP 返回统一中文 label，其中 `pending_photo` 展示为 `未发送照片`，`pending_review` 展示为 `已发送照片`。
    - 结果：无需新增状态迁移即可完成业务语义更新，同时保留对审核流和历史数据的兼容。

13. **招募看板从一次性表格页升级为自动刷新的运营面板**
    - 原因：原页面样式偏 MVP，且接口失败时容易表现为“字段没显示”，不利于运营判断系统状态。
    - 方案：升级为 KPI 卡片 + 数据表 + 详情面板布局，增加状态 badge、空态/错误态与自动刷新提示，并在页面停留期间定时刷新概览、候选人列表和当前详情。

14. **`v1` 激活 Prompt 以仓库文件为准**
   - 原因：运营和开发当前直接在 `prompts/recruiter_v1.md` 中维护“北北”主 Prompt，希望在线企微对话拿到的主 system prompt 与仓库文件保持一致，避免数据库中旧版 `v1` 文本与本地文件脱节。
   - 方案：当 MCP 当前激活的 prompt 版本为 `v1` 时，`GET /prompts/current` 直接读取 `prompts/recruiter_v1.md` 作为主 Prompt，再在同一个 system prompt 末尾拼接所有已审核通过的训练语料；非 `v1` 版本仍使用数据库中发布的内容。

15. **定时任务开关与 env 解析统一**
   - 现象：跟进（每小时）与资料抽取（每 5 分钟扫描、静默 20 分钟后抽取）有时不触发，或配置了 PROFILE_EXTRACTION_ENABLED=1/yes 仍不生效。
   - 原因：`FollowupScheduler` 内 `_profile_enabled` 仅接受字面量 `"true"`，与 app 启动条件（接受 1/true/yes）不一致；未根据 `FOLLOWUP_ENABLED` 门控跟进任务；本地运行未加载项目根目录 `.env`；Docker 未传入调度相关环境变量。

18. **修复微信客服历史消息重放导致消息风暴（95001 + AI 批量调用）**
   - 现象：每次服务重启后，后端对同一用户连续调用 40 次 AI 并尝试批量发送，导致 95001（send msg count limit）错误，用户收到大量重复回复。
   - 根本原因：
     1. `kf_sync_cursor.json` 存在但 `open_kfid` 对应的 cursor 为空（初次或文件被清空），导致 `sync_msg` 不带 cursor 调用时 WeChat 返回从历史开头到当前的全量消息（本次为 40 条）。
     2. `_startup_time` 字段虽然存在但从未被实际用于过滤消息。
     3. 并发 webhook 事件被直接丢弃（`blocking=False`），导致新消息可能错过。
     4. `_send_kf_text` 遇到 95001 直接失败不重试，回复彻底丢失。
   - 修复：
     1. `_startup_time` 改为基于 `open_kfid` 是否存在有效 cursor 来决定是否启用：若无 cursor，记录启动时间；在 `_kf_sync_msg_inner` 内对 `send_time < _startup_time` 的历史消息直接跳过并标记 dedup。
     2. 并发事件改为设置 `_kf_sync_pending` 标志位，当前 sync 完成后立即触发一次补充 sync，不丢新消息。
     3. `_send_kf_text` 遇到 95001 时指数退避重试最多 3 次（2s / 4s / 6s）。

19. **消息聚合窗口（拟人化批量发送）**
   - 背景：用户在短时间内连续发多条消息时，AI 可能仅回复最后一条而漏掉中间条；同时 AI 回复速度远超人类打字速度，会让用户察觉并降低发照意愿。
   - 方案：在 `ChatChannel.produce()` 中，TEXT 类型消息（管理命令 `#` 除外）不立即入队，而是在内存中按 `session_id` 聚合，等待 `msg_batch_window_seconds`（默认 30 秒）后一次性将本窗口内所有文本消息合并（用换行符拼接）作为单条 context 送入处理队列，再走原有的 AI 生成流程。非文本消息（图片、语音等）仍立即入队，不受聚合影响。
   - 配置项：`msg_batch_window_seconds`（整数，默认 30；设为 0 可完全禁用聚合）。
   - 影响：
     1. 同一批次多条消息只触发一次 AI 调用，AI 可看到完整上下文，不再漏消息。
     2. AI 回复的最短等待时间增加至 30 秒（窗口结束后才处理），更接近人类回复节奏。
     3. MCP 的 `log_message` 记录的是合并后的内容，而非每条子消息，日志会显示换行拼接的文本。
     4. `cancel_session` / `cancel_all_session` 同步取消尚未触发的聚合 Timer，不产生孤儿线程。

17. **防止多实例并发导致重复发消息**
   - 现象：用户在微信端收到 AI 的同一条回复被重复发送 10 次以上。
   - 根本原因：运维人员连续执行两次 `python3 app.py >> run.log 2>&1 &`，导致两个进程同时运行。两个进程的 `_processed_msgids` 去重字典各自独立（内存级，不跨进程共享），且共享同一个 cursor 文件，故两进程同时拉取到相同的微信客服消息，各自独立触发 AI 并发送回复。
   - 修复：
     1. `app.py` 启动时写入 `app.pid` 锁文件；若检测到已有进程持有锁则打印提示并退出，彻底防止多实例。
     2. `_kf_sync_msg` 内加 `threading.Lock(non-blocking acquire)` 防止同一进程内微信重试 webhook 导致并发进入同步循环。
     3. `common/log.py` 增加 `/proc` 检测：若 stdout 已被重定向至 `run.log`，则跳过额外的 `FileHandler`，避免每行日志在文件中重复出现两次。
   - 影响：不影响任何业务逻辑，仅改进稳定性与日志可读性。

16. **招募看板发照转化率改为历史口径**
   - 原因：以“今日新增”为分母的转化率在冷启动或低流量时波动大，运营更关注整体转化表现。
   - 方案：`GET /metrics/overview` 新增 `photo_conversion_rate`，计算方式为（历史上曾发照的候选人数 / 历史进入流程的候选人数）× 100；保留 `today_photo_conversion_rate` 与今日相关字段供其他场景使用。Web Console 招募看板展示 `photo_conversion_rate`，文案改为“历史进入流程的候选人中已发照的比例”。
   - 补充：已发照人数按**候选人状态**统计（status 为 pending_review、reviewing、passed、rejected、need_more_photo），不再按 MediaAsset 表统计，避免仅更新了状态为“已发送照片”但未写入 media_assets 的候选人被漏计导致转化率显示 0%。
   - 方案：新增 `_env_bool()` 统一解析 1/true/yes；增加 `_followup_enabled` 仅当为 true 时执行跟进；`app.py` 启动时加载项目根 `.env`；docker-compose 的 agent-runtime 显式传入 FOLLOWUP_* / PROFILE_EXTRACTION_*；未启用时打日志说明需设置上述 env 方可启动调度器。
