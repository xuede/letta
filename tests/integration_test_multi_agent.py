import json

import pytest

from letta import LocalClient, create_client
from letta.functions.functions import derive_openai_json_schema, parse_source_code
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.letta_message import SystemMessage, ToolReturnMessage
from letta.schemas.llm_config import LLMConfig
from letta.schemas.memory import ChatMemory
from letta.schemas.tool import Tool
from letta.services.agent_manager import AgentManager
from tests.helpers.utils import retry_until_success
from tests.utils import wait_for_incoming_message


@pytest.fixture(scope="function")
def client():
    client = create_client()
    client.set_default_llm_config(LLMConfig.default_config("gpt-4o"))
    client.set_default_embedding_config(EmbeddingConfig.default_config(provider="openai"))

    yield client


@pytest.fixture(autouse=True)
def remove_stale_agents(client):
    stale_agents = AgentManager().list_agents(actor=client.user, limit=300)
    for agent in stale_agents:
        client.delete_agent(agent_id=agent.id)


@pytest.fixture(scope="function")
def agent_obj(client: LocalClient):
    """Create a test agent that we can call functions on"""
    send_message_to_agent_and_wait_for_reply_tool_id = client.get_tool_id(name="send_message_to_agent_and_wait_for_reply")
    agent_state = client.create_agent(tool_ids=[send_message_to_agent_and_wait_for_reply_tool_id])

    agent_obj = client.server.load_agent(agent_id=agent_state.id, actor=client.user)
    yield agent_obj

    # client.delete_agent(agent_obj.agent_state.id)


@pytest.fixture(scope="function")
def other_agent_obj(client: LocalClient):
    """Create another test agent that we can call functions on"""
    agent_state = client.create_agent(include_multi_agent_tools=False)

    other_agent_obj = client.server.load_agent(agent_id=agent_state.id, actor=client.user)
    yield other_agent_obj

    client.delete_agent(other_agent_obj.agent_state.id)


@pytest.fixture
def roll_dice_tool(client):
    def roll_dice():
        """
        Rolls a 6 sided die.

        Returns:
            str: The roll result.
        """
        return "Rolled a 5!"

    # Set up tool details
    source_code = parse_source_code(roll_dice)
    source_type = "python"
    description = "test_description"
    tags = ["test"]

    tool = Tool(description=description, tags=tags, source_code=source_code, source_type=source_type)
    derived_json_schema = derive_openai_json_schema(source_code=tool.source_code, name=tool.name)

    derived_name = derived_json_schema["name"]
    tool.json_schema = derived_json_schema
    tool.name = derived_name

    tool = client.server.tool_manager.create_or_update_tool(tool, actor=client.user)

    # Yield the created tool
    yield tool


@retry_until_success(max_attempts=5, sleep_time_seconds=2)
def test_send_message_to_agent(client, agent_obj, other_agent_obj):
    secret_word = "banana"

    # Encourage the agent to send a message to the other agent_obj with the secret string
    client.send_message(
        agent_id=agent_obj.agent_state.id,
        role="user",
        message=f"Use your tool to send a message to another agent with id {other_agent_obj.agent_state.id} to share the secret word: {secret_word}!",
    )

    # Conversation search the other agent
    messages = client.get_messages(other_agent_obj.agent_state.id)
    # Check for the presence of system message
    for m in reversed(messages):
        print(f"\n\n {other_agent_obj.agent_state.id} -> {m.model_dump_json(indent=4)}")
        if isinstance(m, SystemMessage):
            assert secret_word in m.content
            break

    # Search the sender agent for the response from another agent
    in_context_messages = agent_obj.agent_manager.get_in_context_messages(agent_id=agent_obj.agent_state.id, actor=agent_obj.user)
    found = False
    target_snippet = f"{other_agent_obj.agent_state.id} said:"

    for m in in_context_messages:
        if target_snippet in m.content[0].text:
            found = True
            break

    print(f"In context messages of the sender agent (without system):\n\n{"\n".join([m.content[0].text for m in in_context_messages[1:]])}")
    if not found:
        raise Exception(f"Was not able to find an instance of the target snippet: {target_snippet}")

    # Test that the agent can still receive messages fine
    response = client.send_message(agent_id=agent_obj.agent_state.id, role="user", message="So what did the other agent say?")
    print(response.messages)


@retry_until_success(max_attempts=5, sleep_time_seconds=2)
def test_send_message_to_agents_with_tags_simple(client):
    worker_tags_123 = ["worker", "user-123"]
    worker_tags_456 = ["worker", "user-456"]

    # Clean up first from possibly failed tests
    prev_worker_agents = client.server.agent_manager.list_agents(
        client.user, tags=list(set(worker_tags_123 + worker_tags_456)), match_all_tags=True
    )
    for agent in prev_worker_agents:
        client.delete_agent(agent.id)

    secret_word = "banana"

    # Create "manager" agent
    send_message_to_agents_matching_tags_tool_id = client.get_tool_id(name="send_message_to_agents_matching_tags")
    manager_agent_state = client.create_agent(name="manager_agent", tool_ids=[send_message_to_agents_matching_tags_tool_id])
    manager_agent = client.server.load_agent(agent_id=manager_agent_state.id, actor=client.user)

    # Create 3 non-matching worker agents (These should NOT get the message)
    worker_agents_123 = []
    for idx in range(2):
        worker_agent_state = client.create_agent(name=f"not_worker_{idx}", include_multi_agent_tools=False, tags=worker_tags_123)
        worker_agent = client.server.load_agent(agent_id=worker_agent_state.id, actor=client.user)
        worker_agents_123.append(worker_agent)

    # Create 3 worker agents that should get the message
    worker_agents_456 = []
    for idx in range(2):
        worker_agent_state = client.create_agent(name=f"worker_{idx}", include_multi_agent_tools=False, tags=worker_tags_456)
        worker_agent = client.server.load_agent(agent_id=worker_agent_state.id, actor=client.user)
        worker_agents_456.append(worker_agent)

    # Encourage the manager to send a message to the other agent_obj with the secret string
    response = client.send_message(
        agent_id=manager_agent.agent_state.id,
        role="user",
        message=f"Send a message to all agents with tags {worker_tags_456} informing them of the secret word: {secret_word}!",
    )

    for m in response.messages:
        if isinstance(m, ToolReturnMessage):
            tool_response = eval(json.loads(m.tool_return)["message"])
            print(f"\n\nManager agent tool response: \n{tool_response}\n\n")
            assert len(tool_response) == len(worker_agents_456)

            # We can break after this, the ToolReturnMessage after is not related
            break

    # Conversation search the worker agents
    for agent in worker_agents_456:
        messages = client.get_messages(agent.agent_state.id)
        # Check for the presence of system message
        for m in reversed(messages):
            print(f"\n\n {agent.agent_state.id} -> {m.model_dump_json(indent=4)}")
            if isinstance(m, SystemMessage):
                assert secret_word in m.content
                break

    # Ensure it's NOT in the non matching worker agents
    for agent in worker_agents_123:
        messages = client.get_messages(agent.agent_state.id)
        # Check for the presence of system message
        for m in reversed(messages):
            print(f"\n\n {agent.agent_state.id} -> {m.model_dump_json(indent=4)}")
            if isinstance(m, SystemMessage):
                assert secret_word not in m.content

    # Test that the agent can still receive messages fine
    response = client.send_message(agent_id=manager_agent.agent_state.id, role="user", message="So what did the other agents say?")
    print("Manager agent followup message: \n\n" + "\n".join([str(m) for m in response.messages]))

    # Clean up agents
    client.delete_agent(manager_agent_state.id)
    for agent in worker_agents_456 + worker_agents_123:
        client.delete_agent(agent.agent_state.id)


@retry_until_success(max_attempts=5, sleep_time_seconds=2)
def test_send_message_to_agents_with_tags_complex_tool_use(client, roll_dice_tool):
    worker_tags = ["dice-rollers"]

    # Clean up first from possibly failed tests
    prev_worker_agents = client.server.agent_manager.list_agents(client.user, tags=worker_tags, match_all_tags=True)
    for agent in prev_worker_agents:
        client.delete_agent(agent.id)

    # Create "manager" agent
    send_message_to_agents_matching_tags_tool_id = client.get_tool_id(name="send_message_to_agents_matching_tags")
    manager_agent_state = client.create_agent(tool_ids=[send_message_to_agents_matching_tags_tool_id])
    manager_agent = client.server.load_agent(agent_id=manager_agent_state.id, actor=client.user)

    # Create 3 worker agents
    worker_agents = []
    worker_tags = ["dice-rollers"]
    for _ in range(2):
        worker_agent_state = client.create_agent(include_multi_agent_tools=False, tags=worker_tags, tool_ids=[roll_dice_tool.id])
        worker_agent = client.server.load_agent(agent_id=worker_agent_state.id, actor=client.user)
        worker_agents.append(worker_agent)

    # Encourage the manager to send a message to the other agent_obj with the secret string
    broadcast_message = f"Send a message to all agents with tags {worker_tags} asking them to roll a dice for you!"
    response = client.send_message(
        agent_id=manager_agent.agent_state.id,
        role="user",
        message=broadcast_message,
    )

    for m in response.messages:
        if isinstance(m, ToolReturnMessage):
            tool_response = eval(json.loads(m.tool_return)["message"])
            print(f"\n\nManager agent tool response: \n{tool_response}\n\n")
            assert len(tool_response) == len(worker_agents)

            # We can break after this, the ToolReturnMessage after is not related
            break

    # Test that the agent can still receive messages fine
    response = client.send_message(agent_id=manager_agent.agent_state.id, role="user", message="So what did the other agents say?")
    print("Manager agent followup message: \n\n" + "\n".join([str(m) for m in response.messages]))

    # Clean up agents
    client.delete_agent(manager_agent_state.id)
    for agent in worker_agents:
        client.delete_agent(agent.agent_state.id)


@retry_until_success(max_attempts=5, sleep_time_seconds=2)
def test_agents_async_simple(client):
    """
    Test two agents with multi-agent tools sending messages back and forth to count to 5.
    The chain is started by prompting one of the agents.
    """
    # Cleanup from potentially failed previous runs
    existing_agents = client.server.agent_manager.list_agents(client.user)
    for agent in existing_agents:
        client.delete_agent(agent.id)

    # Create two agents with multi-agent tools
    send_message_to_agent_async_tool_id = client.get_tool_id(name="send_message_to_agent_async")
    memory_a = ChatMemory(
        human="Chad - I'm interested in hearing poem.",
        persona="You are an AI agent that can communicate with your agent buddy using `send_message_to_agent_async`, who has some great poem ideas (so I've heard).",
    )
    charles_state = client.create_agent(name="charles", memory=memory_a, tool_ids=[send_message_to_agent_async_tool_id])
    charles = client.server.load_agent(agent_id=charles_state.id, actor=client.user)

    memory_b = ChatMemory(
        human="No human - you are to only communicate with the other AI agent.",
        persona="You are an AI agent that can communicate with your agent buddy using `send_message_to_agent_async`, who is interested in great poem ideas.",
    )
    sarah_state = client.create_agent(name="sarah", memory=memory_b, tool_ids=[send_message_to_agent_async_tool_id])

    # Start the count chain with Agent1
    initial_prompt = f"I want you to talk to the other agent with ID {sarah_state.id} using `send_message_to_agent_async`. Specifically, I want you to ask him for a poem idea, and then craft a poem for me."
    client.send_message(
        agent_id=charles.agent_state.id,
        role="user",
        message=initial_prompt,
    )

    found_in_charles = wait_for_incoming_message(
        client=client,
        agent_id=charles_state.id,
        substring="[Incoming message from agent with ID",
        max_wait_seconds=10,
        sleep_interval=0.5,
    )
    assert found_in_charles, "Charles never received the system message from Sarah (timed out)."

    found_in_sarah = wait_for_incoming_message(
        client=client,
        agent_id=sarah_state.id,
        substring="[Incoming message from agent with ID",
        max_wait_seconds=10,
        sleep_interval=0.5,
    )
    assert found_in_sarah, "Sarah never received the system message from Charles (timed out)."
