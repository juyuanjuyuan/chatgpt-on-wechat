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

8. **招募看板并入原生 Web Console**
   - 原因：避免维护两套入口，降低运营使用复杂度。
   - 方案：在 `channel/web` 增加 Recruit 视图与 `/api/recruit/*` 代理接口，对接 MCP。
