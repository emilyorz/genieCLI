# 撰寫 GenieCLI Skill

Skill 是可被 registry 發現並提供給模型呼叫的小工具。最小單位是一個 `genie/skills/<skill-name>/` 目錄，內含：

```text
genie/skills/my_skill/
├── SKILL.md      # YAML front matter 與給模型的說明
└── __init__.py   # BaseSkill 子類別與 register()
```

`SKILL.md` 的 front matter 是文件與模型說明用的 metadata，可記錄名稱、說明、群組、tier 與選用依賴。Runtime registry 不會用這些欄位設定工具；工具的 `name`、`description`、`group`、`tier` 仍以 `BaseSkill` 子類別為準。Markdown body 會成為 skill instructions，應明確說明何時使用、輸入限制與安全邊界。

```md
---
name: my-skill
description: 對指定文字做唯讀分析。
version: 1.0.0
group: my_skill
tier: core
---

# My Skill

只分析輸入，不寫入檔案或執行外部命令。
```

## 最小實作

```python
from genie.core.arg import Arg
from genie.core.registry import BaseSkill


class MyTool(BaseSkill):
    name = "my_tool"
    description = "分析文字並回傳摘要。"
    group = "my_skill"
    tier = "core"
    args = [
        Arg(name="text", type="str", description="要分析的文字", required=True),
    ]

    def run(self, text: str = "") -> str:
        return f"摘要：{text}"


def register(registry) -> None:
    registry.register(MyTool())
```

`name` 必須是 registry 中唯一的工具名稱。每個 `Arg` 應有正確的 `type`、清楚的 `description`，並標示 `required`；選用參數也要提供合理的 `default`。`run()` 應回傳可供對話使用的字串，並自行處理可預期的外部服務錯誤，避免把未處理例外變成模型結論。

## 建議做法

- 將讀取、寫入或網路副作用說清楚；能唯讀就保持唯讀。
- 參數與回傳內容保持小且可驗證，不把秘密、完整憑證或不必要的大型輸出交給模型。
- 需要外部服務時，提供可行的錯誤訊息與 timeout；不可連線時不可捏造成功結果。
- 為正常輸入、參數驗證與失敗情境新增 focused pytest。
- 以 `genie tools` 檢查內建 skill 的發現結果。目前 loader 以 `genie.skills.<目錄名>` 匯入 Python module；不要把任意 repo 外目錄視為可攜式 plugin 機制。

Trino 相關 skill 應另外遵守 read-only、MCP/direct parity 與 live-evidence 原則；詳見 [Trino 調校指引](trino-tuning-guidance.md) 與 [貢獻指南](../CONTRIBUTING.md)。
