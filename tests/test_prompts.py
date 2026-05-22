from nano_strix.agents.per_file_lib.prompts import (
    ROLE_TEMPLATE,
    ROLE_DEFINITIONS,
    build_system_prompt,
    build_user_prompt_for_file,
)


def test_build_root_system_prompt():
    prompt = build_system_prompt("root")
    assert "root orchestrator" in prompt.lower()
    assert "create_agent" in prompt
    assert "agent_finish" in prompt


def test_build_classify_system_prompt():
    prompt = build_system_prompt("classify")
    assert "classify" in prompt.lower()
    assert "file_search" in prompt


def test_build_analyze_system_prompt():
    prompt = build_system_prompt("analyze")
    assert "analyze" in prompt.lower()
    assert "load_skill" in prompt


def test_all_roles_have_prompts():
    for role in ROLE_DEFINITIONS:
        prompt = build_system_prompt(role)
        assert len(prompt) > 100
        assert ROLE_DEFINITIONS[role]["name"] in prompt


def test_build_user_prompt_includes_file_content():
    prompt = build_user_prompt_for_file(
        file_path="src/login.py",
        priority="high",
        content="def login(): pass",
        scan_findings=[],
        hints={},
    )
    assert "src/login.py" in prompt
    assert "high" in prompt
    assert "def login(): pass" in prompt
    assert "findings" in prompt.lower()
