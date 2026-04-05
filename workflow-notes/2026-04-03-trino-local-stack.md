# 2026-04-03 Trino Local Stack + MCP 整合

## Meta

- date: 2026-04-03
- task level: M
- source request: Sam — Mac mini 搞一套 Trino + MCP 測試環境
- agent target: Emily 直接做
- schema: v1

## Plan

### Requirement Challenge

需求清晰，無矛盾。唯一風險是 Docker 記憶體：目前分配 8GB，用了 ~680MB，Trino 建議 2-3GB，加上其他容器可能吃緊。必要時可限制 Trino JVM heap。

### Scope Summary

搭建本機 Trino + Hive Metastore + MinIO 測試環境，接上 mcp-trino MCP server，灌 sample data，讓 genieCLI 和 Claude Code 都能透過 MCP 或直接 JDBC 對 Trino 做 query。

### Reuse Check

- `docker-compose.yml` 參考 `yuhexiong/deploy-trino-iceberg-hive-metastore-minio-guide`
- mcp-trino 用 `brew install tuannvm/mcp/mcp-trino`
- genieCLI 已有 `trino_linter` + `oracle2trino` skills，加一個 `trino_query` skill 接 MCP 即可

### Minimal Diff Expectation

- Level 1 不動 genieCLI code，純 infra files
- Level 2 新增 1 個 genieCLI skill（`genie/skills/trino_query/`），改 1-2 個 config

### File Impact

**Level 1（infra，新建）：**

- `infra/trino-stack/docker-compose.yml`
- `infra/trino-stack/etc/catalog/iceberg.properties`
- `infra/trino-stack/etc/catalog/memory.properties`
- `infra/trino-stack/init/create-tables.sql`
- `infra/trino-stack/README.md`

**Level 2（genieCLI 整合）：**

- `genie/skills/trino_query/__init__.py` — 新 skill：execute_query + explain
- `~/.config/trino/config.yaml` — mcp-trino profile
- `~/.claude/mcp.json` — 加 trino MCP

### Edge Cases

1. Trino arm64 image 可能比 amd64 慢（JVM warmup）— 接受，不影響功能
2. Hive Metastore 啟動順序依賴 MySQL ready — docker-compose depends_on + healthcheck
3. MinIO bucket 需要先建好才能存 Iceberg data — init script 處理
4. Docker 8GB 分配可能不夠 — Trino JVM heap 限制在 2GB

### Test Matrix

1. **Trino 可連線** — `curl http://localhost:8080/v1/info` 回 200
2. **Memory catalog query 成功** — `mcp-trino query "SELECT 1"` 或 trino CLI
3. **Iceberg table 可建可查** — CREATE TABLE + INSERT + SELECT 成功
4. **mcp-trino MCP 6 tools 可用** — Claude Code session 呼叫 `list_catalogs` 有回應
5. **genieCLI trino_query skill 可用** — `python -m genie --json tools` 包含 trino_query
6. **linter → execute 閉環** — linter 掃出問題 → 修正 → execute 驗證通過

### Out of Scope

- 不做 production hardening（SSL / auth / HA）
- 不做 Trino Query Advisor（Level 3，之後做）
- 不做 schema introspection 自動化
- 不做 CI/CD pipeline for Trino

## Execution Log（2026-04-03 15:00）

### Level 1 — Trino Stack ✅

架構：Trino(:8085) + PostgreSQL(:5433) + MinIO(:9000)

放棄 Hive Metastore（`apache/hive:4.0.1` 缺 MySQL/PostgreSQL JDBC driver，image design issue）。
改用 Iceberg JDBC Catalog（需自己 init `iceberg_tables` schema）。

Docker containers（`infra/trino-stack/docker-compose.yml`）：

- `trino-postgres` — PostgreSQL 16，catalog backend
- `trino-minio` + `trino-minio-init` — S3 storage + bucket init
- `trino` — Trino 480 single-node，JVM heap 2GB，port **8085**（8080 被 OrbStack 佔用）

手動 init schema（Iceberg JDBC catalog 需要）：

```sql
CREATE TABLE iceberg_tables (...);  -- from JdbcCatalog source
CREATE TABLE iceberg_namespace_properties (...);
```

Sample data：`init/create-tables.sql`，4 tables + verify query OK。

mcp-trino 安裝：Homebrew 404（v4.2.0 release 已移除），改用 `go build` 從 source 编。
Binary：`~/.local/bin/mcp-trino`。必要 env：`TRINO_SCHEME=http`（預設 https）。

MCP client config：`~/.claude/mcp.json` → `trino-local` server。

### Level 2 — genieCLI trino_query skill（TODO）
