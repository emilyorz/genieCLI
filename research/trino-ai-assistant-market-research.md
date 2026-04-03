# Trino AI Query Assistant — Market Research

**日期：** 2026-03-23  
**執行人：** Emily  
**目的：** 評估 Trino + AI 查詢助手的市場空間與競品現狀

---

## 1. GitHub

### 搜尋關鍵字

- `trino AI assistant`
- `trino MCP`
- `trino text-to-sql`

### 主要發現

| Repo                   | Stars | 說明                                                                            |
| ---------------------- | ----- | ------------------------------------------------------------------------------- |
| `txn2/mcp-trino`       | 活躍  | MCP server for Trino，最新、最完整，支援 schema introspection + query execution |
| `trinodb/trino`        | 官方  | 本體，無 AI 整合                                                                |
| 其他 text-to-sql repos | 多數  | 通用 Text-to-SQL，未針對 Trino dialect 優化                                     |

### 結論

- MCP（Model Context Protocol）是目前主流整合方式
- `txn2/mcp-trino` 是目前最接近「Trino AI assistant」的開源專案
- 但它只是 MCP server，沒有 query advisor / 優化建議邏輯

---

## 2. X / Twitter

### 搜尋關鍵字

- `trino AI`
- `trino MCP agent`
- `trino query assistant`

### 主要發現

- Trino + MCP + AI agent 的組合正在社群快速擴散（2025 Q4 開始）
- 有工程師分享用 Claude/GPT + Trino MCP 做自動化分析 pipeline
- 討論集中在「schema context 怎麼有效注入 LLM」
- 沒有看到成熟的商業產品，多是個人 hack

### 趨勢

MCP → Trino 的路徑已被社群驗證可行，但還沒有人做成「產品」

---

## 3. Reddit (r/dataengineering)

### 搜尋關鍵字

- `trino query AI assistant`
- `text to sql trino`
- `trino SQL AI`

### 主要討論串

| 標題                                                             | 留言數 | 重點                                                  |
| ---------------------------------------------------------------- | ------ | ----------------------------------------------------- |
| "Text to SQL Agents?"                                            | 38     | 在問有哪些可用工具，沒有明確推薦                      |
| "Good Text-To-SQL solutions?"                                    | 27     | 工具比較，多數反映「能用但不精準」                    |
| "Which of the text-to-sql tools are actually any good?"          | 65     | 社群最大討論，共識：現有工具對特定 SQL dialect 支援差 |
| "Conversational Analytics (Text-to-SQL)"                         | 10     | 企業導入討論                                          |
| "Building a Text-to-SQL AI Tool – What Features Would You Want?" | 20     | 問社群需求，高票回答圍繞 schema awareness             |

### 社群反應模式

1. **痛點明確**：Text-to-SQL 工具會產生「看起來合理但跑不起來」的 SQL
2. **Trino-specific 問題**：沒工具懂 Trino partition pruning、Iceberg time travel、connector 限制
3. **Schema context** 是最大障礙：LLM 不知道哪些 table/column 可用
4. **信任問題**：工程師不敢直接執行 AI 產生的查詢，缺乏 dry-run / explain 機制

---

## 4. 市場空缺分析

### 現有工具的共同缺陷

| 問題               | 現狀                                   |
| ------------------ | -------------------------------------- |
| Trino dialect 支援 | 幾乎沒有專門針對 Trino 的工具          |
| Schema awareness   | 需要手動注入，沒有自動 introspection   |
| Query validation   | 沒有 dry-run / EXPLAIN 自動驗證        |
| 成本估算           | 無法預估查詢掃描量                     |
| Partition 感知     | LLM 不知道 partition key，產生全表掃描 |
| Iceberg 支援       | Time travel / snapshot query 完全缺席  |

### 機會

> **Trino-native Query Advisor**：不只是 Text-to-SQL，而是懂 Trino 的查詢顧問

差異化方向：

1. **Trino dialect-first**：懂 Presto/Trino 語法細節，不產生跑不動的 SQL
2. **Schema-aware**：自動 introspection，知道 catalog/schema/table/column
3. **Query guard**：送出前自動跑 EXPLAIN，估算掃描量
4. **Partition hints**：主動建議加 partition filter
5. **Iceberg-aware**：支援 time travel、snapshot 查詢建議

---

## 5. 競品列表

| 工具               | 類型             | Trino 支援  | 備注                 |
| ------------------ | ---------------- | ----------- | -------------------- |
| `txn2/mcp-trino`   | OSS MCP server   | ✅ 基礎連線 | 無 advisor 邏輯      |
| Vanna.ai           | Text-to-SQL SaaS | ⚠️ 通用     | 需自訓練             |
| SQLAI.ai           | Text-to-SQL SaaS | ⚠️ 通用     | 無 Trino 特化        |
| DataHub Lineage AI | 企業工具         | ⚠️ 部分     | 太重，不輕量         |
| Cursor SQL         | Editor plugin    | ❌          | 無 runtime 整合      |
| 自建 MCP + Claude  | DIY              | ✅ 可       | 需工程能力，無產品化 |

---

## 6. 初步結論

市場空缺明確：**沒有人做 Trino-native 的 AI Query Advisor**。

現有路徑（MCP）已被社群驗證，基礎設施齊備，缺的是：

- 針對 Trino 的 schema introspection 自動化
- Query validation / cost estimation 層
- 使用者友好的介面（不只是 CLI）

---

## 7. 下一步（待 Sam 確認方向）

- [ ] 確認目標使用者：內部 DE 團隊 / 外部商業產品？
- [ ] 確認 MVP scope：CLI / Web UI / VS Code plugin？
- [ ] 評估是否基於 `txn2/mcp-trino` 延伸，還是從零開始
- [ ] Spike：schema introspection + EXPLAIN 整合 POC

---

_Last updated: 2026-03-23 by Emily_
