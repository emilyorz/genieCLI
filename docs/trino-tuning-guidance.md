# Trino 調校指引

本文件說明 GenieCLI 現有 `/trino-research` 提示與診斷採用的方向。它是調校時的判讀清單，不是保證特定改寫一定更快；應以目標 Trino 環境的 EXPLAIN 與 live 量測確認。

## 先看什麼

1. **先縮小掃描。**把選擇性 filter 推到 scan leaf，只取需要欄位；partition 欄位應使用原生型別比較，避免以函數包住而失去 pruning。
2. **先處理真正重複工作。**重複掃描 large raw、fact 或 source table 通常比重複讀取小型 curated、presum 或 dimension table 更值得優先處理。
3. **讓 join 有足夠資訊。**large-large join 通常需要 partitioned hash join；broadcast 僅適合過濾後的 build side 能放入每個 worker 記憶體。統計資料不足時，先建議 `ANALYZE` 或更新 stats，而非猜測 distribution。
4. **保留 dynamic filtering 的條件。**選擇性 dimension filter 與 equi-join key 有助於 Trino 將 runtime filter 推回 probe-side scan；效果仍取決於 connector 與計畫。
5. **以執行症狀定位。**scan bytes 很大但輸出很小，常是 pushdown/pruning 問題；單一 task 特別大可能是 skew；高 peak memory 或 spill 常與過大的 build side、aggregation、window 或 sort 有關。

## CTE 與計畫深度

`WITH`/CTE 是可讀性工具，不應假定為快取或已 materialize 的結果。多層 CTE 加上 JOIN、GROUP BY、window 或 set operation，可能形成很深的計畫、重複 subplan、許多 fragment/exchange，並帶來 blocked time、skew 或 spill。

可優先考慮：

- 將 predicate 與 projection 下推到最早掃描點；
- 縮減 join 前攜帶的欄位，並在適當位置 pre-aggregate；
- 檢查重複 raw/fact scan 與高成本 join/aggregation；
- 用 `EXPLAIN (TYPE DISTRIBUTED)` 檢視 fragment/exchange，以 `EXPLAIN ANALYZE` 檢視 CPU、blocked time、per-task input 差異、dynamic filter 與 output fan-out。

更多 worker 可改善 scan 與 shuffle throughput，但不能修正不良的單一查詢計畫、每 worker 記憶體壓力、hot key、skew 或會 spill 的 stage。

## Materialization 的邊界

下列形狀可作為「考慮 step materialization」的理由：三層以上鏈狀 CTE、多個重 JOIN/GROUP BY 步驟、同一 CTE 被多個 branch 重用，或重複掃描大型 raw/fact table。淺層且只使用一次的 CTE、單純可讀性 CTE，或小型 dimension 的重複讀取，通常優先度較低。

但 CTAS 與 materialized view 是有副作用的策略，並非一般 read-only loop 的自動改寫：

- normal `/trino-research` 只會提出建議，不會自動回傳或執行多 statement DDL；
- 實作前要明確指定 scratch schema、避免名稱衝突、設定 TTL/cleanup、確認 CREATE/DROP 權限與失敗復原方式；
- 不應覆寫來源表；除非使用者明確啟用 materialization mode 並指定 scratch target，不要對使用者 schema 產生 `DROP` 或 `CREATE OR REPLACE`；
- `WITH (cached = TRUE)` 不是 baseline OSS Trino CTE 語法。只有部署的 engine 明確支援且 capability probe 成功時，才可視為版本／fork 專屬能力。

## 常見方向與注意事項

| 訊號 | 可優先檢查的方向 |
| --- | --- |
| `SELECT *`、大 scan、小 output | 明列欄位、predicate/partition pushdown。 |
| computed join key 或 partition key | 盡量在 raw column 比較；若必須正規化，考慮在上游儲存正規化欄位。 |
| huge build side、spill、peak memory 高 | 縮欄位、先 filter/pre-aggregate；勿對大型 build side 盲目 broadcast。 |
| stale/missing stats、異常 join order | 建議 refresh stats／`ANALYZE`，再評估 CBO 選擇。 |
| skewed task input 或 hot key | 定位 hot key、NULL 或 fan-out；依證據考慮 pre-aggregate 或其他資料設計。 |
| `UNION` 去重 | 僅在輸入保證互異時考慮 `UNION ALL`，否則會改變語意。 |

任何跨越 outer join、改寫 correlated subquery、近似 aggregate 或改變去重規則的方案，都必須特別驗證 NULL、重複列與值語意。靜態分析能指出風險，不能替代 live row-value 驗證。

## 官方 Trino 參考

- [SELECT — WITH clause](https://trino.io/docs/current/sql/select.html#with-clause)
- [EXPLAIN](https://trino.io/docs/current/sql/explain.html)
- [EXPLAIN ANALYZE](https://trino.io/docs/current/sql/explain-analyze.html)
- [Cost-based optimizations](https://trino.io/docs/current/optimizer/cost-based-optimizations.html)
- [Pushdown](https://trino.io/docs/current/optimizer/pushdown.html)
- [Dynamic filtering](https://trino.io/docs/current/admin/dynamic-filtering.html)
- [CREATE MATERIALIZED VIEW](https://trino.io/docs/current/sql/create-materialized-view.html)
- [File system cache](https://trino.io/docs/current/object-storage/file-system-cache.html)
