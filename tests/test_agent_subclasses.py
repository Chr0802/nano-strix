from nano_strix.agents.per_file_lib.deep_agent import (
    DeepAnalyseAgent, RootAgent, ClassifyAgent, ScanAgent,
    AnalyzeAgent, CrossLinkAgent, ReviewAgent,
)
from nano_strix.agents.per_file_lib.graph import AgentState


def test_root_agent_role():
    state = AgentState(agent_name="Root", task="orchestrate")
    agent = RootAgent(state=state)
    assert agent.state.role == "root"
    assert "orchestrator" in agent._system_prompt.lower()


def test_classify_agent_role():
    state = AgentState(agent_name="Classifier", task="classify files")
    agent = ClassifyAgent(state=state)
    assert agent.state.role == "classify"


def test_scan_agent_role():
    state = AgentState(agent_name="Scanner", task="scan files")
    agent = ScanAgent(state=state)
    assert agent.state.role == "scan"


def test_analyze_agent_role():
    state = AgentState(agent_name="Analyzer", task="analyze login.py")
    agent = AnalyzeAgent(state=state)
    assert agent.state.role == "analyze"


def test_cross_link_agent_role():
    state = AgentState(agent_name="CrossLinker", task="cross-link")
    agent = CrossLinkAgent(state=state)
    assert agent.state.role == "cross-link"


def test_review_agent_role():
    state = AgentState(agent_name="Reviewer", task="review findings")
    agent = ReviewAgent(state=state)
    assert agent.state.role == "review"


def test_analyze_agent_should_split():
    state = AgentState(agent_name="Analyzer", task="analyze 500 files")
    agent = AnalyzeAgent(state=state)
    assert agent._should_split(file_count=500) is True


def test_analyze_agent_should_not_split_small_count():
    state = AgentState(agent_name="Analyzer", task="analyze 10 files")
    agent = AnalyzeAgent(state=state)
    assert agent._should_split(file_count=10) is False


def test_classify_agent_should_split():
    state = AgentState(agent_name="Classifier", task="classify 200 files")
    agent = ClassifyAgent(state=state)
    assert agent._should_split(file_count=200) is True
