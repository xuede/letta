import pytest

import letta.functions.function_sets.base as base_functions
from letta import LocalClient, create_client
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.llm_config import LLMConfig


@pytest.fixture(scope="function")
def client():
    client = create_client()
    client.set_default_llm_config(LLMConfig.default_config("gpt-4o"))
    client.set_default_embedding_config(EmbeddingConfig.default_config(provider="openai"))

    yield client


@pytest.fixture(scope="function")
def agent_obj(client: LocalClient):
    """Create a test agent that we can call functions on"""
    send_message_to_agent_and_wait_for_reply_tool_id = client.get_tool_id(name="send_message_to_agent_and_wait_for_reply")
    agent_state = client.create_agent(tool_ids=[send_message_to_agent_and_wait_for_reply_tool_id])

    agent_obj = client.server.load_agent(agent_id=agent_state.id, actor=client.user)
    yield agent_obj

    # client.delete_agent(agent_obj.agent_state.id)


def query_in_search_results(search_results, query):
    for result in search_results:
        if query.lower() in result["content"].lower():
            return True
    return False


def test_archival(agent_obj):
    """Test archival memory functions comprehensively."""
    # Test 1: Basic insertion and retrieval
    base_functions.archival_memory_insert(agent_obj, "The cat sleeps on the mat")
    base_functions.archival_memory_insert(agent_obj, "The dog plays in the park")
    base_functions.archival_memory_insert(agent_obj, "Python is a programming language")

    # Test exact text search
    results, _ = base_functions.archival_memory_search(agent_obj, "cat")
    assert query_in_search_results(results, "cat")

    # Test semantic search (should return animal-related content)
    results, _ = base_functions.archival_memory_search(agent_obj, "animal pets")
    assert query_in_search_results(results, "cat") or query_in_search_results(results, "dog")

    # Test unrelated search (should not return animal content)
    results, _ = base_functions.archival_memory_search(agent_obj, "programming computers")
    assert query_in_search_results(results, "python")

    # Test 2: Test pagination
    # Insert more items to test pagination
    for i in range(10):
        base_functions.archival_memory_insert(agent_obj, f"Test passage number {i}")

    # Get first page
    page0_results, next_page = base_functions.archival_memory_search(agent_obj, "Test passage", page=0)
    # Get second page
    page1_results, _ = base_functions.archival_memory_search(agent_obj, "Test passage", page=1, start=next_page)

    assert page0_results != page1_results
    assert query_in_search_results(page0_results, "Test passage")
    assert query_in_search_results(page1_results, "Test passage")

    # Test 3: Test complex text patterns
    base_functions.archival_memory_insert(agent_obj, "Important meeting on 2024-01-15 with John")
    base_functions.archival_memory_insert(agent_obj, "Follow-up meeting scheduled for next week")
    base_functions.archival_memory_insert(agent_obj, "Project deadline is approaching")

    # Search for meeting-related content
    results, _ = base_functions.archival_memory_search(agent_obj, "meeting schedule")
    assert query_in_search_results(results, "meeting")
    assert query_in_search_results(results, "2024-01-15") or query_in_search_results(results, "next week")

    # Test 4: Test error handling
    # Test invalid page number
    try:
        base_functions.archival_memory_search(agent_obj, "test", page="invalid")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_recall_self(client, agent_obj):
    # keyword
    keyword = "banana"
    keyword_backwards = "".join(reversed(keyword))

    # Send messages to agent
    client.send_message(agent_id=agent_obj.agent_state.id, role="user", message="hello")
    client.send_message(agent_id=agent_obj.agent_state.id, role="user", message="what word is '{}' backwards?".format(keyword_backwards))
    client.send_message(agent_id=agent_obj.agent_state.id, role="user", message="tell me a fun fact")

    # Conversation search
    result = base_functions.conversation_search(agent_obj, "banana")
    assert keyword in result
