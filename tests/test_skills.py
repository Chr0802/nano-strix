from pathlib import Path
import tempfile
from nano_strix.skills.loader import SkillLoader


def test_skill_loader_loads_markdown_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        (skills_dir / "test_skill.md").write_text("# Test Skill\nTest content.")
        (skills_dir / "other.md").write_text("# Other\nOther content.")

        loader = SkillLoader(skills_dir)
        loader.load_all()

        assert "test_skill" in loader.list_skills()
        assert "other" in loader.list_skills()
        assert "Test content" in loader.get_skill("test_skill")


def test_skill_loader_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = SkillLoader(Path(tmpdir))
        loader.load_all()
        assert loader.list_skills() == []


def test_skill_loader_get_nonexistent():
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = SkillLoader(Path(tmpdir))
        loader.load_all()
        result = loader.get_skill("nonexistent")
        assert result == ""


def test_load_skill_tool():
    from nano_strix.skills.loader import load_skill
    from nano_strix.agents.deep_analysis_lib.graph import AgentState
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        (skills_dir / "sql_injection.md").write_text("# SQL Injection\nTest injection guide.")
        loader = SkillLoader(skills_dir)
        loader.load_all()

        # Temporarily set the global loader
        import nano_strix.skills.loader as sk_mod
        sk_mod._skill_loader = loader

        state = AgentState(agent_name="TestAgent")
        result = load_skill(state, "sql_injection")
        assert result["success"] is True
        assert result["skill"] == "sql_injection"
        # Agent should have received the skill content as a message
        assert any("sql_injection" in m["content"] for m in state.messages)
