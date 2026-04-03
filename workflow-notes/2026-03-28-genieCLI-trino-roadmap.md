# GenieCLI Trino Tooling Roadmap

> 2026-03-28 | Owner: Sam + Emily | PM Review: Zoe (pending)

## 背景

TSMC Datalake House Tooling Team 需要提供 user 更好的 Trino 使用體驗。
目前 user 的主要痛點：

1. 自己轉 Oracle → Trino，轉出來的 SQL 品質不好
2. 不知道怎麼優化 Trino query 以降低 resource 使用
3. 不熟悉 Trino 語法，不知道怎麼寫

GenieCLI 定位：**Tooling Team 的研究工具 + 可直接提供給 user 的 CLI/API**

## 架構

```
┌──────────────────────────────────┐
│         genie-engine (core)       │
│  - Trino SQL Linter               │
│  - Oracle → Trino Converter       │
│  - Query Optimizer                 │
│  - Knowledge Base (rules/patterns) │
│  - MCP Client (連 Trino)          │
└──────────┬───────────┬───────────┘
           │           │
     CLI (直接用)    HTTP API (給 UI)
```

## Phase 排序

### Phase 1：Trino SQL Linter ⬅️ 現在開始

**不需要 Trino 連線**

User 丟 Trino SQL → 靜態分析 → 結構化 findings

核心 rules：

- Partition pruning 缺失
- 不必要的 `SELECT *`（columnar storage 浪費）
- Implicit cross join（缺 ON 條件）
- Oracle 殘留語法（`NVL`、`DECODE`、`(+)` join、`ROWNUM`、`SYSDATE`）
- 低效 pattern（correlated subquery → JOIN、子查詢 → CTE/window function）
- 型別隱式轉換
- 常見地雷（`COUNT(DISTINCT)` high cardinality、`LIKE '%xxx'` leading wildcard）

產出格式：

```json
{
  "findings": [
    {
      "severity": "high",
      "line": 12,
      "rule": "missing-partition-filter",
      "message": "Table `db.table` is partitioned by `dt`, but no filter on `dt` found",
      "suggestion": "Add WHERE dt = '2026-03-28' or range filter"
    }
  ],
  "score": "C+",
  "summary": "3 high, 2 medium, 1 low"
}
```

### Phase 2：Oracle → Trino 轉換器 ⬅️ 現在開始

**不需要 Trino 連線**

在現有 oracle2trino skill 上加強：

- sqlglot 機械轉 + AI 補修
- 明確標出不能轉的部分（不幻想式翻譯）
- 輸出：converted SQL + unsupported list + manual fix notes + confidence
- 累積 pattern → 回饋到 Phase 1 rules

重點：stored procedure **不做全自動轉換**，做拆解分析 + 逐段建議。

### Phase 3：MCP 接 Trino 🔒 等內網環境

部署 mcp-trino-python 到內網，GenieCLI 接上 MCP client

接上後：

- Phase 1 Linter → schema-aware（知道 partition column、column types）
- Phase 2 轉換器 → 可直接跑 Trino 驗證 parse/execute
- 新增：schema 探索、metadata cache

### Phase 4：Query Optimizer 🔒 需要 Phase 3

吃 `EXPLAIN ANALYZE` → 有 evidence 的優化建議

- 具體指出哪個 stage 最慢、為什麼、怎麼改
- 需要 Trino 連線 + 實際 plan + table stats
- 沒有 evidence 的建議不要講

### Phase 5：Self-iterating 修正 loop 🔒 需要 Phase 3+4

自動：query → 分析 plan → 改寫 → 再跑 → 比對

- 有限狀態機設計，不是自由 agent loop
- Validator 主導，LLM 輔助
- Max retry budget + timeout + human approval gate
- 最後做，因為每一層都要穩

### Phase 6：HTTP API + Web UI

把上面能力包成 FastAPI → 加前端讓非 CLI user 也能用

## 決策紀錄

| 決策                        | 結論                         | 原因                                    |
| --------------------------- | ---------------------------- | --------------------------------------- |
| 自建 vs Fork OSS            | 繼續自建 GenieCLI            | Domain logic 是 moat，不在 agent shell  |
| 先做什麼                    | Trino SQL Linter             | 不需連線、user 最直接的痛點、可立即驗證 |
| Stored procedure 全自動轉換 | 不做                         | Trino 沒有 SP，這是重構問題不是翻譯問題 |
| MCP server 選型             | mcp-trino-python (alaturqua) | Python、Apache 2.0、支援 STDIO+HTTP     |

## Status

- [x] Roadmap 討論完成（Sam + Emily）
- [ ] PM Review（Zoe）
- [ ] Phase 1 Spec
- [ ] Phase 2 Spec 更新
