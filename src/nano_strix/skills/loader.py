from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_skill_loader: SkillLoader | None = None


class SkillLoader:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._skills: dict[str, str] = {}

    def load_all(self) -> dict[str, str]:
        if not self._skills_dir.exists():
            logger.warning("Skills directory not found: %s", self._skills_dir)
            return {}
        for md_file in self._skills_dir.glob("*.md"):
            skill_name = md_file.stem
            content = md_file.read_text(errors="replace")
            self._skills[skill_name] = content
            logger.debug("Loaded skill: %s (%d chars)", skill_name, len(content))
        return dict(self._skills)

    def get_skill(self, name: str) -> str:
        return self._skills.get(name, "")

    def list_skills(self) -> list[str]:
        return sorted(self._skills.keys())


def load_skill(agent_state: Any, skill_name: str) -> dict[str, Any]:
    """Load a vulnerability-specific skill guide into the agent's context.

    Exposed as a @register_tool for LLM agents.
    """
    global _skill_loader
    if _skill_loader is None:
        return {"success": False, "error": "SkillLoader not initialized"}
    content = _skill_loader.get_skill(skill_name)
    if not content:
        return {"success": False, "error": f"Unknown skill: {skill_name}"}
    agent_state.add_message(
        "user",
        f"<specialized_knowledge name=\"{skill_name}\">\n{content}\n</specialized_knowledge>",
    )
    return {"success": True, "skill": skill_name, "size_chars": len(content)}


def set_skill_loader(loader: SkillLoader) -> None:
    global _skill_loader
    _skill_loader = loader
