"""DeepWiki skill — generate wiki docs for code repositories.

Requires a running DeepWiki instance (local or Docker).
Supports GitHub repos, local repos, and Ollama models.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from genie.core.arg import Arg
from genie.core.registry import BaseSkill


DEEPWIKI_API = "http://localhost:8001"


class DeepWikiGenerate(BaseSkill):
    name = "deepwiki_generate"
    description = (
        "Generate wiki documentation for a repository. "
        "Accepts a GitHub URL or a local path (use /app/local-repos/<name> for Docker-mounted repos). "
        "Uses Ollama by default for local analysis."
    )
    group = "analysis"
    tier = "extended"
    args = [
        Arg(name="repo", type="str",
            description="GitHub URL (e.g. https://github.com/owner/repo) or local repo path",
            required=True),
        Arg(name="provider", type="str",
            description="Model provider: ollama, openai, google",
            required=False, default="ollama",
            choices=["ollama", "openai", "google"]),
        Arg(name="model", type="str",
            description="Model name (default: qwen3:1.7b for ollama)",
            required=False, default=""),
        Arg(name="output_dir", type="str",
            description="Directory to save generated wiki (default: ./docs/wiki)",
            required=False, default="./docs/wiki"),
    ]

    def run(self, repo: str = "", provider: str = "ollama", model: str = "",
            output_dir: str = "./docs/wiki") -> str:
        try:
            import requests
        except ImportError:
            return "ERROR: requests library required. pip install requests"

        # Check if DeepWiki is running
        try:
            health = requests.get(f"{DEEPWIKI_API}/health", timeout=5)
            if health.status_code != 200:
                return f"ERROR: DeepWiki not healthy (status {health.status_code})"
        except Exception:
            return (
                "ERROR: DeepWiki not running at localhost:8001. "
                "Start it with: docker run -d -p 3000:3000 -p 8001:8001 deepwiki:ollama-local"
            )

        # Determine repo type
        if repo.startswith("http"):
            repo_type = "github"
            if "gitlab" in repo:
                repo_type = "gitlab"
            elif "bitbucket" in repo:
                repo_type = "bitbucket"
        else:
            repo_type = "local"

        # For local repos, check the structure endpoint first
        if repo_type == "local":
            try:
                structure = requests.get(
                    f"{DEEPWIKI_API}/local_repo/structure",
                    params={"path": repo},
                    timeout=30,
                )
                if structure.status_code != 200:
                    return f"ERROR: Could not read local repo at {repo}: {structure.text[:200]}"
            except Exception as exc:
                return f"ERROR: Failed to read local repo: {exc}"

        # Default model per provider
        if not model:
            model = {
                "ollama": "qwen3:1.7b",
                "openai": "gpt-4o-mini",
                "google": "gemini-2.5-flash",
            }.get(provider, "qwen3:1.7b")

        return (
            f"DeepWiki ready.\n"
            f"Repo: {repo} (type: {repo_type})\n"
            f"Provider: {provider}, Model: {model}\n"
            f"Output: {output_dir}\n"
            f"Note: Full wiki generation runs via WebSocket at ws://localhost:8001/ws/chat. "
            f"Open http://localhost:3000 in browser to generate interactively, "
            f"or use the /export/wiki API endpoint after generation."
        )


class DeepWikiExport(BaseSkill):
    name = "deepwiki_export"
    description = "Export a previously generated wiki as Markdown or JSON"
    group = "analysis"
    tier = "extended"
    args = [
        Arg(name="repo_url", type="str",
            description="The repo URL that was used to generate the wiki",
            required=True),
        Arg(name="format", type="str",
            description="Export format: markdown or json",
            required=False, default="markdown",
            choices=["markdown", "json"]),
        Arg(name="output_path", type="str",
            description="File path to save the exported wiki",
            required=False, default=""),
    ]

    def run(self, repo_url: str = "", format: str = "markdown",
            output_path: str = "") -> str:
        try:
            import requests
        except ImportError:
            return "ERROR: requests library required"

        # Check cache for previously generated wiki
        try:
            cache_resp = requests.get(
                f"{DEEPWIKI_API}/api/wiki_cache",
                params={"repo_url": repo_url},
                timeout=10,
            )
            if cache_resp.status_code != 200 or not cache_resp.json():
                return f"No cached wiki found for {repo_url}. Generate one first."
            cache_data = cache_resp.json()
        except Exception as exc:
            return f"ERROR: Failed to check wiki cache: {exc}"

        # Export
        pages = [
            {"title": p.get("title", ""), "content": p.get("content", "")}
            for p in cache_data.get("wiki_structure", {}).get("pages", [])
        ]
        if not pages:
            return "No pages found in cached wiki."

        try:
            export_resp = requests.post(
                f"{DEEPWIKI_API}/export/wiki",
                json={"repo_url": repo_url, "pages": pages, "format": format},
                timeout=30,
            )
            if export_resp.status_code != 200:
                return f"Export failed: {export_resp.text[:200]}"
        except Exception as exc:
            return f"ERROR: Export request failed: {exc}"

        content = export_resp.text
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(content, encoding="utf-8")
            return f"Wiki exported to {output_path} ({len(content)} chars, {len(pages)} pages)"

        # Return content directly (truncated if large)
        if len(content) > 5000:
            return f"Wiki ({len(pages)} pages, {len(content)} chars):\n\n{content[:5000]}\n\n... [truncated]"
        return f"Wiki ({len(pages)} pages):\n\n{content}"


class DeepWikiStatus(BaseSkill):
    name = "deepwiki_status"
    description = "Check DeepWiki service status and available providers"
    group = "analysis"
    tier = "extended"
    args: list = []

    def run(self) -> str:
        try:
            import requests
        except ImportError:
            return "ERROR: requests library required"

        lines = []
        # Health check
        try:
            resp = requests.get(f"{DEEPWIKI_API}/health", timeout=5)
            lines.append(f"DeepWiki: {'OK' if resp.status_code == 200 else 'DOWN'}")
        except Exception:
            lines.append("DeepWiki: NOT RUNNING (start with docker or python -m api.main)")
            return "\n".join(lines)

        # Check Ollama
        try:
            ollama_resp = requests.get("http://localhost:11434/api/tags", timeout=5)
            if ollama_resp.status_code == 200:
                models = [m["name"] for m in ollama_resp.json().get("models", [])]
                lines.append(f"Ollama: OK ({len(models)} models: {', '.join(models[:5])})")
            else:
                lines.append("Ollama: NOT RUNNING")
        except Exception:
            lines.append("Ollama: NOT RUNNING")

        return "\n".join(lines)


def register(registry) -> None:
    registry.register(DeepWikiGenerate())
    registry.register(DeepWikiExport())
    registry.register(DeepWikiStatus())
