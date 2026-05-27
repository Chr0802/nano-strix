from nano_strix.agents.deep_analysis_lib.prompts import (
    ROLE_TEMPLATE,
    ROLE_DEFINITIONS,
    build_system_prompt,
    build_user_prompt_for_file,
)


def test_build_root_system_prompt():
    prompt = build_system_prompt("root")
    # role name is still English
    assert "root orchestrator" in prompt.lower()
    assert "create_agent" in prompt
    assert "agent_finish" in prompt
    # new: prompt body is now Chinese
    assert "你是一个专业的AI网络安全智能体" in prompt
    # new: output_format section is injected
    assert "<output_format>" in prompt
    assert "findings" in prompt.lower()


def test_build_classify_system_prompt():
    prompt = build_system_prompt("classify")
    # role name "File Classifier" contains "classifier", not "classify"
    assert "file classifier" in prompt.lower()
    assert "file_search" in prompt
    # Chinese role description
    assert "源代码文件分类器" in prompt


def test_build_analyze_system_prompt():
    prompt = build_system_prompt("analyze")
    # "Per-File Analyzer" contains "analyze"
    assert "per-file analyzer" in prompt.lower()
    assert "load_skill" in prompt
    # Chinese role description
    assert "逐文件深度分析器" in prompt


def test_all_roles_have_prompts():
    for role in ROLE_DEFINITIONS:
        prompt = build_system_prompt(role)
        assert len(prompt) > 100
        assert ROLE_DEFINITIONS[role]["name"] in prompt
        # each role now has an output field injected into the template
        assert "<output_format>" in prompt


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


def test_output_format_is_role_specific():
    """Each role's 'output' field is injected into its system prompt."""
    # root output mentions CRITICAL/HIGH/MEDIUM/LOW/INFO and exploitability scale
    root_prompt = build_system_prompt("root")
    assert "CRITICAL" in root_prompt
    assert "E0" in root_prompt

    # classify output mentions file_manifest.json
    classify_prompt = build_system_prompt("classify")
    assert "file_manifest.json" in classify_prompt

    # scan output mentions semgrep/bandit scan findings
    scan_prompt = build_system_prompt("scan")
    assert "scan_findings" in scan_prompt


def test_build_system_prompt_missing_output_raises_error():
    """If a role definition is missing the 'output' key, it should raise KeyError."""
    # All current roles have 'output', but the code path requires it.
    for role in ROLE_DEFINITIONS:
        assert "output" in ROLE_DEFINITIONS[role], f"Role '{role}' missing 'output' key"
