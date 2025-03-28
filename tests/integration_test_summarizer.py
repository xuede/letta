import json
import os
import uuid
from datetime import datetime
from typing import List

import pytest

from letta import create_client
from letta.agent import Agent
from letta.client.client import LocalClient
from letta.errors import ContextWindowExceededError
from letta.llm_api.helpers import calculate_summarizer_cutoff
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.enums import MessageRole
from letta.schemas.letta_message_content import TextContent
from letta.schemas.llm_config import LLMConfig
from letta.schemas.message import Message
from letta.settings import summarizer_settings
from letta.streaming_interface import StreamingRefreshCLIInterface
from tests.helpers.endpoints_helper import EMBEDDING_CONFIG_PATH
from tests.helpers.utils import cleanup

# constants
LLM_CONFIG_DIR = "tests/configs/llm_model_configs"
SUMMARY_KEY_PHRASE = "The following is a summary"

test_agent_name = f"test_client_{str(uuid.uuid4())}"

# TODO: these tests should include looping through LLM providers, since behavior may vary across providers
# TODO: these tests should add function calls into the summarized message sequence:W


@pytest.fixture(scope="module")
def client():
    client = create_client()
    # client.set_default_llm_config(LLMConfig.default_config("gpt-4o-mini"))
    client.set_default_llm_config(LLMConfig.default_config("gpt-4o-mini"))
    client.set_default_embedding_config(EmbeddingConfig.default_config(provider="openai"))

    yield client


@pytest.fixture(scope="module")
def agent_state(client):
    # Generate uuid for agent name for this example
    agent_state = client.create_agent(name=test_agent_name)
    yield agent_state

    client.delete_agent(agent_state.id)


# Sample data setup
def generate_message(role: str, text: str = None, tool_calls: List = None) -> Message:
    """Helper to generate a Message object."""
    return Message(
        id="message-" + str(uuid.uuid4()),
        role=MessageRole(role),
        content=[TextContent(text=text or f"{role} message text")],
        created_at=datetime.utcnow(),
        tool_calls=tool_calls or [],
    )


def test_cutoff_calculation(mocker):
    """Test basic scenarios where the function calculates the cutoff correctly."""
    # Arrange
    logger = mocker.Mock()  # Mock logger
    messages = [
        generate_message("system"),
        generate_message("user"),
        generate_message("assistant"),
        generate_message("user"),
        generate_message("assistant"),
    ]
    mocker.patch("letta.settings.summarizer_settings.desired_memory_token_pressure", 0.5)
    mocker.patch("letta.settings.summarizer_settings.evict_all_messages", False)

    # Basic tests
    token_counts = [4, 2, 8, 2, 2]
    cutoff = calculate_summarizer_cutoff(messages, token_counts, logger)
    assert cutoff == 3
    assert messages[cutoff - 1].role == MessageRole.assistant

    token_counts = [4, 2, 2, 2, 2]
    cutoff = calculate_summarizer_cutoff(messages, token_counts, logger)
    assert cutoff == 5
    assert messages[cutoff - 1].role == MessageRole.assistant

    token_counts = [2, 2, 3, 2, 2]
    cutoff = calculate_summarizer_cutoff(messages, token_counts, logger)
    assert cutoff == 3
    assert messages[cutoff - 1].role == MessageRole.assistant

    # Evict all messages
    # Should give the end of the token_counts, even though it is not necessary (can just evict up to the 100)
    mocker.patch("letta.settings.summarizer_settings.evict_all_messages", True)
    token_counts = [1, 1, 100, 1, 1]
    cutoff = calculate_summarizer_cutoff(messages, token_counts, logger)
    assert cutoff == 5
    assert messages[cutoff - 1].role == MessageRole.assistant

    # Don't evict all messages with same token_counts, cutoff now should be at the 100
    # Should give the end of the token_counts, even though it is not necessary (can just evict up to the 100)
    mocker.patch("letta.settings.summarizer_settings.evict_all_messages", False)
    cutoff = calculate_summarizer_cutoff(messages, token_counts, logger)
    assert cutoff == 3
    assert messages[cutoff - 1].role == MessageRole.assistant

    # Set `keep_last_n_messages`
    mocker.patch("letta.settings.summarizer_settings.keep_last_n_messages", 3)
    token_counts = [4, 2, 2, 2, 2]
    cutoff = calculate_summarizer_cutoff(messages, token_counts, logger)
    assert cutoff == 2
    assert messages[cutoff - 1].role == MessageRole.user


def test_summarize_many_messages_basic(client, mock_e2b_api_key_none):
    small_context_llm_config = LLMConfig.default_config("gpt-4o-mini")
    small_context_llm_config.context_window = 3000
    small_agent_state = client.create_agent(
        name="small_context_agent",
        llm_config=small_context_llm_config,
    )
    for _ in range(10):
        client.user_message(
            agent_id=small_agent_state.id,
            message="hi " * 60,
        )
    client.delete_agent(small_agent_state.id)


def test_summarize_large_message_does_not_loop_infinitely(client, mock_e2b_api_key_none):
    small_context_llm_config = LLMConfig.default_config("gpt-4o-mini")
    small_context_llm_config.context_window = 2000
    small_agent_state = client.create_agent(
        name="super_small_context_agent",
        llm_config=small_context_llm_config,
    )
    with pytest.raises(ContextWindowExceededError, match=f"Ran summarizer {summarizer_settings.max_summarizer_retries}"):
        client.user_message(
            agent_id=small_agent_state.id,
            message="hi " * 1000,
        )
    client.delete_agent(small_agent_state.id)


def test_summarize_messages_inplace(client, agent_state, mock_e2b_api_key_none):
    """Test summarization via sending the summarize CLI command or via a direct call to the agent object"""
    # First send a few messages (5)
    response = client.user_message(
        agent_id=agent_state.id,
        message="Hey, how's it going? What do you think about this whole shindig",
    ).messages
    assert response is not None and len(response) > 0
    print(f"test_summarize: response={response}")

    response = client.user_message(
        agent_id=agent_state.id,
        message="Any thoughts on the meaning of life?",
    ).messages
    assert response is not None and len(response) > 0
    print(f"test_summarize: response={response}")

    response = client.user_message(agent_id=agent_state.id, message="Does the number 42 ring a bell?").messages
    assert response is not None and len(response) > 0
    print(f"test_summarize: response={response}")

    response = client.user_message(
        agent_id=agent_state.id,
        message="Would you be surprised to learn that you're actually conversing with an AI right now?",
    ).messages
    assert response is not None and len(response) > 0
    print(f"test_summarize: response={response}")

    # reload agent object
    agent_obj = client.server.load_agent(agent_id=agent_state.id, actor=client.user)

    agent_obj.summarize_messages_inplace()


def test_auto_summarize(client, mock_e2b_api_key_none):
    """Test that the summarizer triggers by itself"""
    small_context_llm_config = LLMConfig.default_config("gpt-4o-mini")
    small_context_llm_config.context_window = 4000

    small_agent_state = client.create_agent(
        name="small_context_agent",
        llm_config=small_context_llm_config,
    )

    try:

        def summarize_message_exists(messages: List[Message]) -> bool:
            for message in messages:
                if message.content[0].text and "The following is a summary of the previous" in message.content[0].text:
                    print(f"Summarize message found after {message_count} messages: \n {message.content[0].text}")
                    return True
            return False

        MAX_ATTEMPTS = 10
        message_count = 0
        while True:

            # send a message
            response = client.user_message(
                agent_id=small_agent_state.id,
                message="What is the meaning of life?",
            )
            message_count += 1

            print(f"Message {message_count}: \n\n{response.messages}" + "--------------------------------")

            # check if the summarize message is inside the messages
            assert isinstance(client, LocalClient), "Test only works with LocalClient"
            in_context_messages = client.server.agent_manager.get_in_context_messages(agent_id=small_agent_state.id, actor=client.user)
            print("SUMMARY", summarize_message_exists(in_context_messages))
            if summarize_message_exists(in_context_messages):
                break

            if message_count > MAX_ATTEMPTS:
                raise Exception(f"Summarize message not found after {message_count} messages")

    finally:
        client.delete_agent(small_agent_state.id)


@pytest.mark.parametrize(
    "config_filename",
    [
        "openai-gpt-4o.json",
        "azure-gpt-4o-mini.json",
        "claude-3-5-haiku.json",
        # "groq.json", TODO: Support groq, rate limiting currently makes it impossible to test
        # "gemini-pro.json", TODO: Gemini is broken
    ],
)
def test_summarizer(config_filename, client, agent_state):
    namespace = uuid.NAMESPACE_DNS
    agent_name = str(uuid.uuid5(namespace, f"integration-test-summarizer-{config_filename}"))

    # Get the LLM config
    filename = os.path.join(LLM_CONFIG_DIR, config_filename)
    config_data = json.load(open(filename, "r"))

    # Create client and clean up agents
    llm_config = LLMConfig(**config_data)
    embedding_config = EmbeddingConfig(**json.load(open(EMBEDDING_CONFIG_PATH)))
    client = create_client()
    client.set_default_llm_config(llm_config)
    client.set_default_embedding_config(embedding_config)
    cleanup(client=client, agent_uuid=agent_name)

    # Create agent
    agent_state = client.create_agent(name=agent_name, llm_config=llm_config, embedding_config=embedding_config)
    full_agent_state = client.get_agent(agent_id=agent_state.id)
    letta_agent = Agent(
        interface=StreamingRefreshCLIInterface(),
        agent_state=full_agent_state,
        first_message_verify_mono=False,
        user=client.user,
    )

    # Make conversation
    messages = [
        "Did you know that honey never spoils? Archaeologists have found pots of honey in ancient Egyptian tombs that are over 3,000 years old and still perfectly edible.",
        "Octopuses have three hearts, and two of them stop beating when they swim.",
    ]

    for m in messages:
        letta_agent.step_user_message(
            user_message_str=m,
            first_message=False,
            skip_verify=False,
            stream=False,
        )

    # Invoke a summarize
    letta_agent.summarize_messages_inplace()
    in_context_messages = client.get_in_context_messages(agent_state.id)
    assert SUMMARY_KEY_PHRASE in in_context_messages[1].content[0].text, f"Test failed for config: {config_filename}"
