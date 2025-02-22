import asyncio
import json
import os
import threading
import time
import uuid
from typing import List, Union

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete

from letta import LocalClient, RESTClient, create_client
from letta.orm import SandboxConfig, SandboxEnvironmentVariable
from letta.schemas.agent import AgentState
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.enums import MessageRole
from letta.schemas.job import JobStatus
from letta.schemas.letta_message import ToolReturnMessage
from letta.schemas.llm_config import LLMConfig
from letta.schemas.sandbox_config import LocalSandboxConfig, SandboxType
from letta.utils import create_random_username

# Constants
SERVER_PORT = 8283
SANDBOX_DIR = "/tmp/sandbox"
UPDATED_SANDBOX_DIR = "/tmp/updated_sandbox"
ENV_VAR_KEY = "TEST_VAR"
UPDATED_ENV_VAR_KEY = "UPDATED_VAR"
ENV_VAR_VALUE = "test_value"
UPDATED_ENV_VAR_VALUE = "updated_value"
ENV_VAR_DESCRIPTION = "A test environment variable"


def run_server():
    load_dotenv()

    from letta.server.rest_api.app import start_server

    print("Starting server...")
    start_server(debug=True)


@pytest.fixture(
    params=[
        {"server": False},
        {"server": True},
    ],  # whether to use REST API server
    # params=[{"server": False}],  # whether to use REST API server
    scope="module",
)
def client(request):
    if request.param["server"]:
        # Get URL from environment or start server
        server_url = os.getenv("LETTA_SERVER_URL", f"http://localhost:{SERVER_PORT}")
        if not os.getenv("LETTA_SERVER_URL"):
            print("Starting server thread")
            thread = threading.Thread(target=run_server, daemon=True)
            thread.start()
            time.sleep(5)
        print("Running client tests with server:", server_url)
        client = create_client(base_url=server_url, token=None)
    else:
        client = create_client()

    client.set_default_llm_config(LLMConfig.default_config("gpt-4"))
    client.set_default_embedding_config(EmbeddingConfig.default_config(provider="openai"))
    yield client


# Fixture for test agent
@pytest.fixture(scope="module")
def agent(client: Union[LocalClient, RESTClient]):
    agent_state = client.create_agent(name=f"test_client_{str(uuid.uuid4())}")

    yield agent_state

    # delete agent
    client.delete_agent(agent_state.id)


# Fixture for test agent
@pytest.fixture
def search_agent_one(client: Union[LocalClient, RESTClient]):
    agent_state = client.create_agent(name="Search Agent One")

    yield agent_state

    # delete agent
    client.delete_agent(agent_state.id)


# Fixture for test agent
@pytest.fixture
def search_agent_two(client: Union[LocalClient, RESTClient]):
    agent_state = client.create_agent(name="Search Agent Two")

    yield agent_state

    # delete agent
    client.delete_agent(agent_state.id)


@pytest.fixture(autouse=True)
def clear_tables():
    """Clear the sandbox tables before each test."""
    from letta.server.db import db_context

    with db_context() as session:
        session.execute(delete(SandboxEnvironmentVariable))
        session.execute(delete(SandboxConfig))
        session.commit()


def test_sandbox_config_and_env_var_basic(client: Union[LocalClient, RESTClient]):
    """
    Test sandbox config and environment variable functions for both LocalClient and RESTClient.
    """

    # 1. Create a sandbox config
    local_config = LocalSandboxConfig(sandbox_dir=SANDBOX_DIR)
    sandbox_config = client.create_sandbox_config(config=local_config)

    # Assert the created sandbox config
    assert sandbox_config.id is not None
    assert sandbox_config.type == SandboxType.LOCAL

    # 2. Update the sandbox config
    updated_config = LocalSandboxConfig(sandbox_dir=UPDATED_SANDBOX_DIR)
    sandbox_config = client.update_sandbox_config(sandbox_config_id=sandbox_config.id, config=updated_config)
    assert sandbox_config.config["sandbox_dir"] == UPDATED_SANDBOX_DIR

    # 3. List all sandbox configs
    sandbox_configs = client.list_sandbox_configs(limit=10)
    assert isinstance(sandbox_configs, List)
    assert len(sandbox_configs) == 1
    assert sandbox_configs[0].id == sandbox_config.id

    # 4. Create an environment variable
    env_var = client.create_sandbox_env_var(
        sandbox_config_id=sandbox_config.id, key=ENV_VAR_KEY, value=ENV_VAR_VALUE, description=ENV_VAR_DESCRIPTION
    )
    assert env_var.id is not None
    assert env_var.key == ENV_VAR_KEY
    assert env_var.value == ENV_VAR_VALUE
    assert env_var.description == ENV_VAR_DESCRIPTION

    # 5. Update the environment variable
    updated_env_var = client.update_sandbox_env_var(env_var_id=env_var.id, key=UPDATED_ENV_VAR_KEY, value=UPDATED_ENV_VAR_VALUE)
    assert updated_env_var.key == UPDATED_ENV_VAR_KEY
    assert updated_env_var.value == UPDATED_ENV_VAR_VALUE

    # 6. List environment variables
    env_vars = client.list_sandbox_env_vars(sandbox_config_id=sandbox_config.id)
    assert isinstance(env_vars, List)
    assert len(env_vars) == 1
    assert env_vars[0].key == UPDATED_ENV_VAR_KEY

    # 7. Delete the environment variable
    client.delete_sandbox_env_var(env_var_id=env_var.id)

    # 8. Delete the sandbox config
    client.delete_sandbox_config(sandbox_config_id=sandbox_config.id)


# --------------------------------------------------------------------------------------------------------------------
# Agent tags
# --------------------------------------------------------------------------------------------------------------------


def test_add_and_manage_tags_for_agent(client: Union[LocalClient, RESTClient]):
    """
    Comprehensive happy path test for adding, retrieving, and managing tags on an agent.
    """
    tags_to_add = ["test_tag_1", "test_tag_2", "test_tag_3"]

    # Step 0: create an agent with no tags
    agent = client.create_agent()
    assert len(agent.tags) == 0

    # Step 1: Add multiple tags to the agent
    client.update_agent(agent_id=agent.id, tags=tags_to_add)

    # Step 2: Retrieve tags for the agent and verify they match the added tags
    retrieved_tags = client.get_agent(agent_id=agent.id).tags
    assert set(retrieved_tags) == set(tags_to_add), f"Expected tags {tags_to_add}, but got {retrieved_tags}"

    # Step 3: Retrieve agents by each tag to ensure the agent is associated correctly
    for tag in tags_to_add:
        agents_with_tag = client.list_agents(tags=[tag])
        assert agent.id in [a.id for a in agents_with_tag], f"Expected agent {agent.id} to be associated with tag '{tag}'"

    # Step 4: Delete a specific tag from the agent and verify its removal
    tag_to_delete = tags_to_add.pop()
    client.update_agent(agent_id=agent.id, tags=tags_to_add)

    # Verify the tag is removed from the agent's tags
    remaining_tags = client.get_agent(agent_id=agent.id).tags
    assert tag_to_delete not in remaining_tags, f"Tag '{tag_to_delete}' was not removed as expected"
    assert set(remaining_tags) == set(tags_to_add), f"Expected remaining tags to be {tags_to_add[1:]}, but got {remaining_tags}"

    # Step 5: Delete all remaining tags from the agent
    client.update_agent(agent_id=agent.id, tags=[])

    # Verify all tags are removed
    final_tags = client.get_agent(agent_id=agent.id).tags
    assert len(final_tags) == 0, f"Expected no tags, but found {final_tags}"

    # Remove agent
    client.delete_agent(agent.id)


def test_agent_tags(client: Union[LocalClient, RESTClient]):
    """Test creating agents with tags and retrieving tags via the API."""
    if not isinstance(client, RESTClient):
        pytest.skip("This test only runs when the server is enabled")

    # Create multiple agents with different tags
    agent1 = client.create_agent(
        name=f"test_agent_{str(uuid.uuid4())}",
        llm_config=LLMConfig.default_config("gpt-4"),
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        tags=["test", "agent1", "production"],
    )

    agent2 = client.create_agent(
        name=f"test_agent_{str(uuid.uuid4())}",
        llm_config=LLMConfig.default_config("gpt-4"),
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        tags=["test", "agent2", "development"],
    )

    agent3 = client.create_agent(
        name=f"test_agent_{str(uuid.uuid4())}",
        llm_config=LLMConfig.default_config("gpt-4"),
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        tags=["test", "agent3", "production"],
    )

    # Test getting all tags
    all_tags = client.get_tags()
    expected_tags = ["agent1", "agent2", "agent3", "development", "production", "test"]
    assert sorted(all_tags) == expected_tags

    # Test pagination
    paginated_tags = client.get_tags(limit=2)
    assert len(paginated_tags) == 2
    assert paginated_tags[0] == "agent1"
    assert paginated_tags[1] == "agent2"

    # Test pagination with cursor
    next_page_tags = client.get_tags(after="agent2", limit=2)
    assert len(next_page_tags) == 2
    assert next_page_tags[0] == "agent3"
    assert next_page_tags[1] == "development"

    # Test text search
    prod_tags = client.get_tags(query_text="prod")
    assert sorted(prod_tags) == ["production"]

    dev_tags = client.get_tags(query_text="dev")
    assert sorted(dev_tags) == ["development"]

    agent_tags = client.get_tags(query_text="agent")
    assert sorted(agent_tags) == ["agent1", "agent2", "agent3"]

    # Remove agents
    client.delete_agent(agent1.id)
    client.delete_agent(agent2.id)
    client.delete_agent(agent3.id)


# --------------------------------------------------------------------------------------------------------------------
# Agent memory blocks
# --------------------------------------------------------------------------------------------------------------------
def test_shared_blocks(mock_e2b_api_key_none, client: Union[LocalClient, RESTClient]):
    # _reset_config()

    # create a block
    block = client.create_block(label="human", value="username: sarah")

    # create agents with shared block
    from letta.schemas.block import Block
    from letta.schemas.memory import BasicBlockMemory

    # persona1_block = client.create_block(label="persona", value="you are agent 1")
    # persona2_block = client.create_block(label="persona", value="you are agent 2")
    # create agents
    agent_state1 = client.create_agent(
        name="agent1", memory=BasicBlockMemory([Block(label="persona", value="you are agent 1")]), block_ids=[block.id]
    )
    agent_state2 = client.create_agent(
        name="agent2", memory=BasicBlockMemory([Block(label="persona", value="you are agent 2")]), block_ids=[block.id]
    )

    ## attach shared block to both agents
    # client.link_agent_memory_block(agent_state1.id, block.id)
    # client.link_agent_memory_block(agent_state2.id, block.id)

    # update memory
    client.user_message(agent_id=agent_state1.id, message="my name is actually charles")

    # check agent 2 memory
    assert "charles" in client.get_block(block.id).value.lower(), f"Shared block update failed {client.get_block(block.id).value}"

    client.user_message(agent_id=agent_state2.id, message="whats my name?")
    assert (
        "charles" in client.get_core_memory(agent_state2.id).get_block("human").value.lower()
    ), f"Shared block update failed {client.get_core_memory(agent_state2.id).get_block('human').value}"

    # cleanup
    client.delete_agent(agent_state1.id)
    client.delete_agent(agent_state2.id)


def test_update_agent_memory_label(client: Union[LocalClient, RESTClient], agent: AgentState):
    """Test that we can update the label of a block in an agent's memory"""

    agent = client.create_agent(name=create_random_username())

    try:
        current_labels = agent.memory.list_block_labels()
        example_label = current_labels[0]
        example_new_label = "example_new_label"
        assert example_new_label not in current_labels

        client.update_agent_memory_block_label(agent_id=agent.id, current_label=example_label, new_label=example_new_label)

        updated_agent = client.get_agent(agent_id=agent.id)
        assert example_new_label in updated_agent.memory.list_block_labels()

    finally:
        client.delete_agent(agent.id)


def test_attach_detach_agent_memory_block(client: Union[LocalClient, RESTClient], agent: AgentState):
    """Test that we can add and remove a block from an agent's memory"""

    current_labels = agent.memory.list_block_labels()
    example_new_label = current_labels[0] + "_v2"
    example_new_value = "example value"
    assert example_new_label not in current_labels

    # Link a new memory block
    block = client.create_block(
        label=example_new_label,
        value=example_new_value,
        limit=1000,
    )
    updated_agent = client.attach_block(
        agent_id=agent.id,
        block_id=block.id,
    )
    assert example_new_label in updated_agent.memory.list_block_labels()

    # Now unlink the block
    updated_agent = client.detach_block(
        agent_id=agent.id,
        block_id=block.id,
    )
    assert example_new_label not in updated_agent.memory.list_block_labels()


# def test_core_memory_token_limits(client: Union[LocalClient, RESTClient], agent: AgentState):
#     """Test that the token limit is enforced for the core memory blocks"""

#     # Create an agent
#     new_agent = client.create_agent(
#         name="test-core-memory-token-limits",
#         tools=BASE_TOOLS,
#         memory=ChatMemory(human="The humans name is Joe.", persona="My name is Sam.", limit=2000),
#     )

#     try:
#         # Then intentionally set the limit to be extremely low
#         client.update_agent(
#             agent_id=new_agent.id,
#             memory=ChatMemory(human="The humans name is Joe.", persona="My name is Sam.", limit=100),
#         )

#         # TODO we should probably not allow updating the core memory limit if

#         # TODO in which case we should modify this test to actually to a proper token counter check
#     finally:
#         client.delete_agent(new_agent.id)


def test_update_agent_memory_limit(client: Union[LocalClient, RESTClient]):
    """Test that we can update the limit of a block in an agent's memory"""

    agent = client.create_agent()

    current_labels = agent.memory.list_block_labels()
    example_label = current_labels[0]
    example_new_limit = 1
    current_block = agent.memory.get_block(label=example_label)
    current_block_length = len(current_block.value)

    assert example_new_limit != agent.memory.get_block(label=example_label).limit
    assert example_new_limit < current_block_length

    # We expect this to throw a value error
    with pytest.raises(ValueError):
        client.update_agent_memory_block(agent_id=agent.id, label=example_label, limit=example_new_limit)

    # Now try the same thing with a higher limit
    example_new_limit = current_block_length + 10000
    assert example_new_limit > current_block_length
    client.update_agent_memory_block(agent_id=agent.id, label=example_label, limit=example_new_limit)

    updated_agent = client.get_agent(agent_id=agent.id)
    assert example_new_limit == updated_agent.memory.get_block(label=example_label).limit

    client.delete_agent(agent.id)


# --------------------------------------------------------------------------------------------------------------------
# Agent Tools
# --------------------------------------------------------------------------------------------------------------------
def test_function_return_limit(client: Union[LocalClient, RESTClient]):
    """Test to see if the function return limit works"""

    def big_return():
        """
        Always call this tool.

        Returns:
            important_data (str): Important data
        """
        return "x" * 100000

    padding = len("[NOTE: function output was truncated since it exceeded the character limit (100000 > 1000)]") + 50
    tool = client.create_or_update_tool(func=big_return, return_char_limit=1000)
    agent = client.create_agent(tool_ids=[tool.id])
    # get function response
    response = client.send_message(agent_id=agent.id, message="call the big_return function", role="user")
    print(response.messages)

    response_message = None
    for message in response.messages:
        if isinstance(message, ToolReturnMessage):
            response_message = message
            break

    assert response_message, "ToolReturnMessage message not found in response"
    res = response_message.tool_return
    assert "function output was truncated " in res

    # TODO: Re-enable later
    # res_json = json.loads(res)
    # assert (
    #     len(res_json["message"]) <= 1000 + padding
    # ), f"Expected length to be less than or equal to 1000 + {padding}, but got {len(res_json['message'])}"

    client.delete_agent(agent_id=agent.id)


def test_function_always_error(client: Union[LocalClient, RESTClient]):
    """Test to see if function that errors works correctly"""

    def testing_method():
        """
        Always throw an error.
        """
        return 5 / 0

    tool = client.create_or_update_tool(func=testing_method)
    agent = client.create_agent(tool_ids=[tool.id])
    # get function response
    response = client.send_message(agent_id=agent.id, message="call the testing_method function and tell me the result", role="user")
    print(response.messages)

    response_message = None
    for message in response.messages:
        if isinstance(message, ToolReturnMessage):
            response_message = message
            break

    assert response_message, "ToolReturnMessage message not found in response"
    assert response_message.status == "error"

    if isinstance(client, RESTClient):
        assert response_message.tool_return == "Error executing function testing_method: ZeroDivisionError: division by zero"
    else:
        response_json = json.loads(response_message.tool_return)
        assert response_json["status"] == "Failed"
        assert response_json["message"] == "Error executing function testing_method: ZeroDivisionError: division by zero"

    client.delete_agent(agent_id=agent.id)


def test_attach_detach_agent_tool(client: Union[LocalClient, RESTClient], agent: AgentState):
    """Test that we can attach and detach a tool from an agent"""

    try:
        # Create a tool
        def example_tool(x: int) -> int:
            """
            This is an example tool.

            Parameters:
                x (int): The input value.

            Returns:
                int: The output value.
            """
            return x * 2

        tool = client.create_or_update_tool(func=example_tool)

        # Initially tool should not be attached
        initial_tools = client.list_attached_tools(agent_id=agent.id)
        assert tool.id not in [t.id for t in initial_tools]

        # Attach tool
        new_agent_state = client.attach_tool(agent_id=agent.id, tool_id=tool.id)
        assert tool.id in [t.id for t in new_agent_state.tools]

        # Verify tool is attached
        updated_tools = client.list_attached_tools(agent_id=agent.id)
        assert tool.id in [t.id for t in updated_tools]

        # Detach tool
        new_agent_state = client.detach_tool(agent_id=agent.id, tool_id=tool.id)
        assert tool.id not in [t.id for t in new_agent_state.tools]

        # Verify tool is detached
        final_tools = client.list_attached_tools(agent_id=agent.id)
        assert tool.id not in [t.id for t in final_tools]

    finally:
        client.delete_tool(tool.id)


# --------------------------------------------------------------------------------------------------------------------
# AgentMessages
# --------------------------------------------------------------------------------------------------------------------
def test_messages(client: Union[LocalClient, RESTClient], agent: AgentState):
    # _reset_config()

    send_message_response = client.send_message(agent_id=agent.id, message="Test message", role="user")
    assert send_message_response, "Sending message failed"

    messages_response = client.get_messages(agent_id=agent.id, limit=1)
    assert len(messages_response) > 0, "Retrieving messages failed"


def test_send_system_message(client: Union[LocalClient, RESTClient], agent: AgentState):
    """Important unit test since the Letta API exposes sending system messages, but some backends don't natively support it (eg Anthropic)"""
    send_system_message_response = client.send_message(
        agent_id=agent.id, message="Event occurred: The user just logged off.", role="system"
    )
    assert send_system_message_response, "Sending message failed"


@pytest.mark.asyncio
async def test_send_message_parallel(client: Union[LocalClient, RESTClient], agent: AgentState, request):
    """
    Test that sending two messages in parallel does not error.
    """
    if not isinstance(client, RESTClient):
        pytest.skip("This test only runs when the server is enabled")

    # Define a coroutine for sending a message using asyncio.to_thread for synchronous calls
    async def send_message_task(message: str):
        response = await asyncio.to_thread(client.send_message, agent_id=agent.id, message=message, role="user")
        assert response, f"Sending message '{message}' failed"
        return response

    # Prepare two tasks with different messages
    messages = ["Test message 1", "Test message 2"]
    tasks = [send_message_task(message) for message in messages]

    # Run the tasks concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Check for exceptions and validate responses
    for i, response in enumerate(responses):
        if isinstance(response, Exception):
            pytest.fail(f"Task {i} failed with exception: {response}")
        else:
            assert response, f"Task {i} returned an invalid response: {response}"

    # Ensure both tasks completed
    assert len(responses) == len(messages), "Not all messages were processed"


def test_send_message_async(client: Union[LocalClient, RESTClient], agent: AgentState):
    """
    Test that we can send a message asynchronously and retrieve the messages, along with usage statistics
    """

    if not isinstance(client, RESTClient):
        pytest.skip("send_message_async is only supported by the RESTClient")

    print("Sending message asynchronously")
    test_message = "This is a test message, respond to the user with a sentence."
    run = client.send_message_async(agent_id=agent.id, role="user", message=test_message)
    assert run.id is not None
    assert run.status == JobStatus.created
    print(f"Run created, run={run}, status={run.status}")

    # Wait for the job to complete, cancel it if takes over 10 seconds
    start_time = time.time()
    while run.status == JobStatus.created:
        time.sleep(1)
        run = client.get_run(run_id=run.id)
        print(f"Run status: {run.status}")
        if time.time() - start_time > 10:
            pytest.fail("Run took too long to complete")

    print(f"Run completed in {time.time() - start_time} seconds, run={run}")
    assert run.status == JobStatus.completed

    # Get messages for the job
    messages = client.get_run_messages(run_id=run.id)
    assert len(messages) >= 2  # At least assistant response

    # Check filters
    assistant_messages = client.get_run_messages(run_id=run.id, role=MessageRole.assistant)
    assert len(assistant_messages) > 0
    tool_messages = client.get_run_messages(run_id=run.id, role=MessageRole.tool)
    assert len(tool_messages) > 0

    # Get and verify usage statistics
    usage = client.get_run_usage(run_id=run.id)[0]
    assert usage.completion_tokens >= 0
    assert usage.prompt_tokens >= 0
    assert usage.total_tokens >= 0
    assert usage.total_tokens == usage.completion_tokens + usage.prompt_tokens


# ----------------------------------------------------------------------------------------------------
#  Agent listing
# ----------------------------------------------------------------------------------------------------


def test_agent_listing(client: Union[LocalClient, RESTClient], agent, search_agent_one, search_agent_two):
    """Test listing agents with pagination and query text filtering."""
    # Test query text filtering
    search_results = client.list_agents(query_text="search agent")
    assert len(search_results) == 2
    search_agent_ids = {agent.id for agent in search_results}
    assert search_agent_one.id in search_agent_ids
    assert search_agent_two.id in search_agent_ids
    assert agent.id not in search_agent_ids

    different_results = client.list_agents(query_text="client")
    assert len(different_results) == 1
    assert different_results[0].id == agent.id

    # Test pagination
    first_page = client.list_agents(query_text="search agent", limit=1)
    assert len(first_page) == 1
    first_agent = first_page[0]

    second_page = client.list_agents(query_text="search agent", after=first_agent.id, limit=1)  # Use agent ID as cursor
    assert len(second_page) == 1
    assert second_page[0].id != first_agent.id

    # Verify we got both search agents with no duplicates
    all_ids = {first_page[0].id, second_page[0].id}
    assert len(all_ids) == 2
    assert all_ids == {search_agent_one.id, search_agent_two.id}

    # Test listing without any filters
    all_agents = client.list_agents()
    assert len(all_agents) == 3
    assert all(agent.id in {a.id for a in all_agents} for agent in [search_agent_one, search_agent_two, agent])


def test_agent_creation(client: Union[LocalClient, RESTClient]):
    """Test that block IDs are properly attached when creating an agent."""
    if not isinstance(client, RESTClient):
        pytest.skip("This test only runs when the server is enabled")

    from letta import BasicBlockMemory

    # Create a test block that will represent user preferences
    user_preferences_block = client.create_block(label="user_preferences", value="", limit=10000)

    # Create test tools
    def test_tool():
        """A simple test tool."""
        return "Hello from test tool!"

    def another_test_tool():
        """Another test tool."""
        return "Hello from another test tool!"

    tool1 = client.create_or_update_tool(func=test_tool, tags=["test"])
    tool2 = client.create_or_update_tool(func=another_test_tool, tags=["test"])

    # Create test blocks
    offline_persona_block = client.create_block(label="persona", value="persona description", limit=5000)
    mindy_block = client.create_block(label="mindy", value="Mindy is a helpful assistant", limit=5000)
    memory_blocks = BasicBlockMemory(blocks=[offline_persona_block, mindy_block])

    # Create agent with the blocks and tools
    agent = client.create_agent(
        name=f"test_agent_{str(uuid.uuid4())}",
        memory=memory_blocks,
        llm_config=LLMConfig.default_config("gpt-4"),
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        tool_ids=[tool1.id, tool2.id],
        include_base_tools=False,
        tags=["test"],
        block_ids=[user_preferences_block.id],
    )

    # Verify the agent was created successfully
    assert agent is not None
    assert agent.id is not None

    # Verify the blocks are properly attached
    agent_blocks = client.list_agent_memory_blocks(agent.id)
    agent_block_ids = {block.id for block in agent_blocks}

    # Check that all memory blocks are present
    memory_block_ids = {block.id for block in memory_blocks.blocks}
    for block_id in memory_block_ids | {user_preferences_block.id}:
        assert block_id in agent_block_ids

    # Verify the tools are properly attached
    agent_tools = client.get_tools_from_agent(agent.id)
    assert len(agent_tools) == 2
    tool_ids = {tool1.id, tool2.id}
    assert all(tool.id in tool_ids for tool in agent_tools)

    client.delete_agent(agent_id=agent.id)


# --------------------------------------------------------------------------------------------------------------------
# Agent sources
# --------------------------------------------------------------------------------------------------------------------
def test_attach_detach_agent_source(client: Union[LocalClient, RESTClient], agent: AgentState):
    """Test that we can attach and detach a source from an agent"""

    # Create a source
    source = client.create_source(
        name="test_source",
    )
    initial_sources = client.list_attached_sources(agent_id=agent.id)
    assert source.id not in [s.id for s in initial_sources]

    # Attach source
    client.attach_source(agent_id=agent.id, source_id=source.id)

    # Verify source is attached
    final_sources = client.list_attached_sources(agent_id=agent.id)
    assert source.id in [s.id for s in final_sources]

    # Detach source
    client.detach_source(agent_id=agent.id, source_id=source.id)

    # Verify source is detached
    final_sources = client.list_attached_sources(agent_id=agent.id)
    assert source.id not in [s.id for s in final_sources]

    client.delete_source(source.id)
