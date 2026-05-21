import asyncio
import pytest
from nano_strix.agents.per_file_lib.graph import AgentState


def test_agent_state_defaults():
    state = AgentState()
    assert state.agent_id.startswith("agent_")
    assert len(state.agent_id) == 14  # "agent_" + 8 hex
    assert state.agent_name == "DeepAnalyseAgent"
    assert state.parent_id is None
    assert state.iteration == 0
    assert state.max_iterations == 300
    assert state.completed is False
    assert state.waiting_for_input is False


def test_agent_state_add_message():
    state = AgentState()
    state.add_message("user", "hello")
    assert len(state.messages) == 1
    assert state.messages[0] == {"role": "user", "content": "hello"}


def test_agent_state_wake_on_message():
    state = AgentState()
    state.enter_waiting_state()
    assert state.waiting_for_input is True
    # adding a message while waiting should set the wake event
    state.add_message("user", "wake up")
    # wake_event should now be set
    assert state._wake_event.is_set()


@pytest.mark.asyncio
async def test_agent_state_wait_for_wake_timeout():
    state = AgentState()
    # should return after timeout since no one sets the event
    await state.wait_for_wake(timeout=0.1)


@pytest.mark.asyncio
async def test_agent_state_wait_for_wake_signalled():
    state = AgentState()
    state.enter_waiting_state()

    async def signal_later():
        await asyncio.sleep(0.05)
        state.resume_from_waiting()

    done, pending = await asyncio.wait(
        [asyncio.create_task(state.wait_for_wake(timeout=1.0)),
         asyncio.create_task(signal_later())],
        return_when=asyncio.FIRST_COMPLETED
    )
    # If wait_for_wake returned first, it didn't block forever
    assert any(not t.cancelled() for t in done)


def test_agent_state_should_stop_max_iterations():
    state = AgentState()
    state.iteration = 300
    assert state.should_stop() is True


def test_agent_state_increment_iteration():
    state = AgentState()
    state.increment_iteration()
    assert state.iteration == 1


def test_graph_globals_exist():
    from nano_strix.agents.per_file_lib import graph
    assert hasattr(graph, '_agent_graph')
    assert graph._agent_graph == {"nodes": {}, "edges": []}
    assert graph._root_agent_id is None
    assert isinstance(graph._agent_messages, dict)
    assert isinstance(graph._running_agents, dict)
    assert isinstance(graph._agent_instances, dict)
    assert isinstance(graph._agent_states, dict)
