# Trino Local Stack

genieCLI 本機 Trino 測試環境。4 個容器 + mcp-trino MCP server。

## 架構

```
Trino (:8080) ←→ Hive Metastore (:9083) ←→ MySQL (:3307)
    ↕
MinIO (:9000, :9001 console)
    ↕
mcp-trino (STDIO) → Claude Code / genieCLI
```

## 快速開始

```bash
# 1. 啟動 stack
cd infra/trino-stack
docker compose up -d

# 2. 等 Trino healthy（約 30-60 秒）
docker compose ps

# 3. 灌 sample data
docker exec -i trino trino < init/create-tables.sql

# 4. 驗證
docker exec -it trino trino --execute "SELECT * FROM iceberg.warehouse.employees LIMIT 5"
```

## 安裝 mcp-trino

```bash
brew install tuannvm/mcp/mcp-trino

# 驗證
export TRINO_HOST=localhost TRINO_PORT=8080 TRINO_USER=trino
mcp-trino query "SELECT 1"
```

## Catalogs

| Catalog | 用途           | 儲存      |
| ------- | -------------- | --------- |
| iceberg | Iceberg tables | MinIO S3  |
| memory  | 臨時測試       | in-memory |

## Sample Tables

| Table                           | 筆數 | 用途                       |
| ------------------------------- | ---- | -------------------------- |
| iceberg.warehouse.employees     | 10   | partition filter 測試      |
| iceberg.warehouse.orders        | 7    | date partition + joins     |
| iceberg.warehouse.oracle_legacy | 4    | Oracle pattern linter 測試 |
| memory.test.numbers             | 5    | 快速驗證                   |

## 管理

```bash
docker compose stop     # 停止（保留 data）
docker compose start    # 重啟
docker compose down     # 停止 + 移除 containers
docker compose down -v  # 停止 + 清除所有 data
```

## MinIO Console

http://localhost:9001 — 帳號 `minioadmin` / `minioadmin`

## 資源用量

| 容器           | RAM 估計  |
| -------------- | --------- |
| Trino          | ~2 GB     |
| Hive Metastore | ~512 MB   |
| MySQL          | ~256 MB   |
| MinIO          | ~256 MB   |
| **合計**       | **~3 GB** |
