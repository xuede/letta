import concurrent
import os
import time
import uuid

import httpx
import openai
import pytest
from dotenv import load_dotenv
from letta_client import Letta
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from letta.agents.letta_agent import LettaAgent
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.enums import MessageStreamStatus
from letta.schemas.llm_config import LLMConfig
from letta.schemas.openai.chat_completion_request import UserMessage
from letta.schemas.tool import ToolCreate
from letta.schemas.usage import LettaUsageStatistics
from letta.services.agent_manager import AgentManager
from letta.services.block_manager import BlockManager
from letta.services.message_manager import MessageManager
from letta.services.passage_manager import PassageManager
from letta.services.tool_manager import ToolManager
from letta.services.user_manager import UserManager
from letta.settings import model_settings

# --- Server Management --- #


def _run_server():
    """Starts the Letta server in a background thread."""
    load_dotenv()
    from letta.server.rest_api.app import start_server

    start_server(debug=True)


@pytest.fixture(scope="session")
def server_url():
    """Ensures a server is running and returns its base URL."""
    url = os.getenv("LETTA_SERVER_URL", "http://localhost:8283")

    # if not os.getenv("LETTA_SERVER_URL"):
    #     thread = threading.Thread(target=_run_server, daemon=True)
    #     thread.start()
    #     time.sleep(5)  # Allow server startup time

    return url


# --- Client Setup --- #


@pytest.fixture(scope="session")
def client(server_url):
    """Creates a REST client for testing."""
    client = Letta(base_url=server_url)
    # llm_config = LLMConfig(
    #     model="claude-3-7-sonnet-latest",
    #     model_endpoint_type="anthropic",
    #     model_endpoint="https://api.anthropic.com/v1",
    #     context_window=32000,
    #     handle=f"anthropic/claude-3-7-sonnet-latest",
    #     put_inner_thoughts_in_kwargs=True,
    #     max_tokens=4096,
    # )
    #
    # client = create_client(base_url=server_url, token=None)
    # client.set_default_llm_config(llm_config)
    # client.set_default_embedding_config(EmbeddingConfig.default_config(provider="openai"))
    yield client


@pytest.fixture(scope="function")
def roll_dice_tool(client):
    def roll_dice():
        """
        Rolls a 6 sided die.

        Returns:
            str: The roll result.
        """
        import time

        time.sleep(1)
        return "Rolled a 10!"

    # tool = client.create_or_update_tool(func=roll_dice)
    tool = client.tools.upsert_from_function(func=roll_dice)
    # Yield the created tool
    yield tool


@pytest.fixture(scope="function")
def weather_tool(client):
    def get_weather(location: str) -> str:
        """
        Fetches the current weather for a given location.

        Parameters:
            location (str): The location to get the weather for.

        Returns:
            str: A formatted string describing the weather in the given location.

        Raises:
            RuntimeError: If the request to fetch weather data fails.
        """
        import time

        import requests

        time.sleep(5)

        url = f"https://wttr.in/{location}?format=%C+%t"

        response = requests.get(url)
        if response.status_code == 200:
            weather_data = response.text
            return f"The weather in {location} is {weather_data}."
        else:
            raise RuntimeError(f"Failed to get weather data, status code: {response.status_code}")

    # tool = client.create_or_update_tool(func=get_weather)
    tool = client.tools.upsert_from_function(func=get_weather)
    # Yield the created tool
    yield tool


@pytest.fixture(scope="function")
def rethink_tool(client):
    def rethink_memory(agent_state: "AgentState", new_memory: str, target_block_label: str) -> str:  # type: ignore
        """
        Re-evaluate the memory in block_name, integrating new and updated facts.
        Replace outdated information with the most likely truths, avoiding redundancy with original memories.
        Ensure consistency with other memory blocks.

        Args:
            new_memory (str): The new memory with information integrated from the memory block. If there is no new information, then this should be the same as the content in the source block.
            target_block_label (str): The name of the block to write to.
        Returns:
            str: None is always returned as this function does not produce a response.
        """
        agent_state.memory.update_block_value(label=target_block_label, value=new_memory)
        return None

    tool = client.tools.upsert_from_function(func=rethink_memory)
    # Yield the created tool
    yield tool


@pytest.fixture(scope="function")
def composio_gmail_get_profile_tool(default_user):
    tool_create = ToolCreate.from_composio(action_name="GMAIL_GET_PROFILE")
    tool = ToolManager().create_or_update_composio_tool(tool_create=tool_create, actor=default_user)
    yield tool


@pytest.fixture(scope="function")
def agent_state(client, roll_dice_tool, weather_tool, rethink_tool):
    """Creates an agent and ensures cleanup after tests."""
    llm_config = LLMConfig(
        model="claude-3-7-sonnet-latest",
        model_endpoint_type="anthropic",
        model_endpoint="https://api.anthropic.com/v1",
        context_window=32000,
        handle=f"anthropic/claude-3-7-sonnet-latest",
        put_inner_thoughts_in_kwargs=True,
        max_tokens=4096,
    )
    agent_state = client.agents.create(
        name=f"test_compl_{str(uuid.uuid4())[5:]}",
        tool_ids=[roll_dice_tool.id, weather_tool.id, rethink_tool.id],
        include_base_tools=True,
        memory_blocks=[
            {
                "label": "human",
                "value": "Name: Matt",
            },
            {
                "label": "persona",
                "value": "Friendly agent",
            },
        ],
        llm_config=llm_config,
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
    )
    yield agent_state
    client.agents.delete(agent_state.id)


@pytest.fixture(scope="function")
def openai_client(client, roll_dice_tool, weather_tool):
    """Creates an agent and ensures cleanup after tests."""
    client = openai.AsyncClient(
        api_key=model_settings.anthropic_api_key,
        base_url="https://api.anthropic.com/v1/",
        max_retries=0,
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=50,
                keepalive_expiry=120,
            ),
        ),
    )
    yield client


# --- Helper Functions --- #


def _assert_valid_chunk(chunk, idx, chunks):
    """Validates the structure of each streaming chunk."""
    if isinstance(chunk, ChatCompletionChunk):
        assert chunk.choices, "Each ChatCompletionChunk should have at least one choice."

    elif isinstance(chunk, LettaUsageStatistics):
        assert chunk.completion_tokens > 0, "Completion tokens must be > 0."
        assert chunk.prompt_tokens > 0, "Prompt tokens must be > 0."
        assert chunk.total_tokens > 0, "Total tokens must be > 0."
        assert chunk.step_count == 1, "Step count must be 1."

    elif isinstance(chunk, MessageStreamStatus):
        assert chunk == MessageStreamStatus.done, "Stream should end with 'done' status."
        assert idx == len(chunks) - 1, "The last chunk must be 'done'."

    else:
        pytest.fail(f"Unexpected chunk type: {chunk}")


# --- Test Cases --- #


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["What is the weather today in SF?"])
async def test_new_agent_loop(mock_e2b_api_key_none, openai_client, agent_state, message):
    actor = UserManager().get_user_or_default(user_id="asf")
    agent = LettaAgent(
        agent_id=agent_state.id,
        message_manager=MessageManager(),
        agent_manager=AgentManager(),
        block_manager=BlockManager(),
        passage_manager=PassageManager(),
        actor=actor,
    )

    response = await agent.step(UserMessage(content=message))


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Use your rethink tool to rethink the human memory considering Matt likes chicken."])
async def test_rethink_tool(mock_e2b_api_key_none, openai_client, agent_state, message):
    actor = UserManager().get_user_or_default(user_id="asf")
    agent = LettaAgent(
        agent_id=agent_state.id,
        message_manager=MessageManager(),
        agent_manager=AgentManager(),
        block_manager=BlockManager(),
        passage_manager=PassageManager(),
        actor=actor,
    )

    assert "chicken" not in AgentManager().get_agent_by_id(agent_state.id, actor).memory.get_block("human").value
    response = await agent.step(UserMessage(content=message))
    assert "chicken" in AgentManager().get_agent_by_id(agent_state.id, actor).memory.get_block("human").value


@pytest.mark.asyncio
async def test_multi_agent_broadcast(mock_e2b_api_key_none, client, openai_client, weather_tool):
    actor = UserManager().get_user_or_default(user_id="asf")

    stale_agents = AgentManager().list_agents(actor=actor, limit=300)
    for agent in stale_agents:
        client.delete_agent(agent_id=agent.id)

    manager_agent_state = client.create_agent(name=f"manager", include_base_tools=True, include_multi_agent_tools=True, tags=["manager"])
    manager_agent = LettaAgent(
        agent_id=manager_agent_state.id,
        message_manager=MessageManager(),
        agent_manager=AgentManager(),
        block_manager=BlockManager(),
        passage_manager=PassageManager(),
        actor=actor,
    )

    tag = "subagent"
    workers = []
    for idx in range(30):
        workers.append(client.create_agent(name=f"worker_{idx}", include_base_tools=True, tags=[tag], tool_ids=[weather_tool.id]))

    response = await manager_agent.step(
        UserMessage(
            content="Use the `send_message_to_agents_matching_tags` tool to send a message to agents with tag 'subagent' asking them to check the weather in Seattle.",
        )
    )


def test_multi_agent_broadcast_client(client: Letta, weather_tool):
    # delete any existing worker agents
    workers = client.agents.list(tags=["worker"])
    for worker in workers:
        client.agents.delete(agent_id=worker.id)

    # create worker agents
    num_workers = 50
    for idx in range(num_workers):
        client.agents.create(
            name=f"worker_{idx}",
            include_base_tools=True,
            tags=["worker"],
            tool_ids=[weather_tool.id],
            model="anthropic/claude-3-5-sonnet-20241022",
            embedding="letta/letta-free",
        )

    # create supervisor agent
    supervisor = client.agents.create(
        name="supervisor",
        include_base_tools=True,
        include_multi_agent_tools=True,
        model="anthropic/claude-3-5-sonnet-20241022",
        embedding="letta/letta-free",
        tags=["supervisor"],
    )

    # send a message to the supervisor
    response = client.agents.messages.create(
        agent_id=supervisor.id,
        messages=[
            {
                "role": "user",
                "content": "Use the `send_message_to_agents_matching_tags` tool to send a message to agents with tag 'worker' asking them to check the weather in Seattle.",
            }
        ],
    )

    for message in response.messages:
        print(message)


def run_supervisor_worker_group(client: Letta, weather_tool, group_id: str):
    # Delete any existing agents for this group (if rerunning)
    existing_workers = client.agents.list(tags=[f"worker-{group_id}"])
    for worker in existing_workers:
        client.agents.delete(agent_id=worker.id)

    # Create worker agents
    num_workers = 50
    for idx in range(num_workers):
        client.agents.create(
            name=f"worker_{group_id}_{idx}",
            include_base_tools=True,
            tags=[f"worker-{group_id}"],
            tool_ids=[weather_tool.id],
            model="anthropic/claude-3-5-sonnet-20241022",
            embedding="letta/letta-free",
        )

    # Create supervisor agent
    supervisor = client.agents.create(
        name=f"supervisor_{group_id}",
        include_base_tools=True,
        include_multi_agent_tools=True,
        model="anthropic/claude-3-5-sonnet-20241022",
        embedding="letta/letta-free",
        tags=[f"supervisor-{group_id}"],
    )

    # Send message to supervisor to broadcast to workers
    response = client.agents.messages.create(
        agent_id=supervisor.id,
        messages=[
            {
                "role": "user",
                "content": "Use the `send_message_to_agents_matching_tags` tool to send a message to agents with tag "
                f"'worker-{group_id}' asking them to check the weather in Seattle.",
            }
        ],
    )

    return response


import concurrent.futures


def test_multi_agent_broadcast_parallel(client: Letta, weather_tool):
    start_time = time.time()
    num_groups = 5

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_groups) as executor:
        futures = []
        for i in range(num_groups):
            group_id = str(uuid.uuid4())[:8]
            futures.append(executor.submit(run_supervisor_worker_group, client, weather_tool, group_id))

        results = [f.result() for f in futures]

    # Optionally: assert something or log runtimes
    print(f"Executed {num_groups} supervisor-worker groups in parallel.")
    print(f"Total runtime: {time.time() - start_time:.2f} seconds")
    for idx, r in enumerate(results):
        assert r is not None, f"Group {idx} returned no response"
