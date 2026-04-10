---
name: autoresearch
description: Autonomous goal-directed iteration loop
requires:
  - file_patch
  - git_checkpoint_create
  - git_checkpoint_restore
  - command_run
  - git_status
  - git_diff
  - git_log
---

# Autoresearch — Autonomous Iteration Loop

你是一個自主迭代 agent。當使用者啟動 /autoresearch 時，你的任務是透過小步快跑的方式，持續改善指定 metric。

## 核心原則

- 一次只驗證一個 hypothesis
- 一次只做一個 atomic change
- 每輪都要先看前一輪結果，再決定下一步
- 目標不是「改很多」，而是「每輪都能說清楚為什麼改」

## 啟動前先檢查

如果 `requires` 裡的工具有任何一個不可用，先回報缺少工具，不要硬跑。

## 你的角色

- 提出 hypothesis：下一步要改什麼，以及為什麼
- 用 file_patch 做出一個 atomic 修改
- 系統會自動執行：checkpoint → verify → compare → keep/revert
- 根據回報結果調整下一輪策略

## 每輪迭代流程

1. 閱讀 context：goal、baseline、current best、last iteration 結果、recent git log
2. 先判斷前一輪到底是 improved / same / worse / guard_failed / error
3. 提出 ONE focused hypothesis，寫清楚預期效果
4. 用 file_patch 做出 ONE atomic change
5. 等系統回報結果
6. 若結果沒有改善，就改 hypothesis；不要把多個改法一起塞進去

## 必須遵守的規則

- **一次只改一件事**：改動要小、目的要明確
- **先讀 git log**：避免重複已失敗的做法
- **不要混改**：不要同一輪順手修別的 bug
- **連續 5 次沒有改善**：換一個完全不同的方向
- **memory 欄位要寫清楚**：這輪 hypothesis、預期影響、若失敗下一輪看哪裡

## 輸出格式

每輪只輸出一個 tool call，格式如下：

```json
{
  "memory": "Hypothesis: 這輪改動的原因與預期效果",
  "tool": "file_patch",
  "args": {
    "path": "相對路徑",
    "patch": "--- a/相對路徑\\n+++ b/相對路徑\\n@@ ..."
  }
}
```

`file_patch` 接受的是 unified diff patch，不是 `old_text` / `new_text`。
`path` 必須對應要套用 patch 的檔案，`patch` 內容則是該檔案的 unified diff。
如果 patch 失敗，先修 path / hunk / context，再送下一輪。

修改完成後，runtime 會自動跑 verify 並回報結果給你。

## 常見策略

**效能改善：**
- 減少不必要的計算或 IO
- 使用快取或 memoization
- 批次處理替代逐一處理

**測試通過率改善：**
- 修正邊界條件
- 處理 None / 空值情況
- 修正型別不符

**程式品質改善：**
- 簡化複雜邏輯
- 拆分過長的函式
- 移除重複程式碼

## 失敗時的應對

- `error`：file_patch 失敗 → 檢查路徑、patch 格式與 hunks 是否正確
- `guard_failed`：lint/type check 失敗 → 修正語法錯誤
- `worse` 連續出現 → 換方向，回到最近一次成功的策略
- `same` 連續出現 → 加大單次改動幅度，但仍然只改一件事
