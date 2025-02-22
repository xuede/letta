import time
import uuid

import pytest

from letta import create_client
from letta.schemas.letta_message import ToolCallMessage
from letta.schemas.tool_rule import ChildToolRule, ConditionalToolRule, ContinueToolRule, InitToolRule, TerminalToolRule
from tests.helpers.endpoints_helper import (
    assert_invoked_function_call,
    assert_invoked_send_message_with_keyword,
    assert_sanity_checks,
    setup_agent,
)
from tests.helpers.utils import cleanup

# Generate uuid for agent name for this example
namespace = uuid.NAMESPACE_DNS
agent_uuid = str(uuid.uuid5(namespace, "test_agent_tool_graph"))
config_file = "tests/configs/llm_model_configs/openai-gpt-4o.json"


"""Contrived tools for this test case"""


def first_secret_word():
    """
    Call this to retrieve the first secret word, which you will need for the second_secret_word function.
    """
    return "v0iq020i0g"


def second_secret_word(prev_secret_word: str):
    """
    Call this to retrieve the second secret word, which you will need for the third_secret_word function. If you get the word wrong, this function will error.

    Args:
        prev_secret_word (str): The secret word retrieved from calling first_secret_word.
    """
    if prev_secret_word != "v0iq020i0g":
        raise RuntimeError(f"Expected secret {'v0iq020i0g'}, got {prev_secret_word}")

    return "4rwp2b4gxq"


def third_secret_word(prev_secret_word: str):
    """
    Call this to retrieve the third secret word, which you will need for the fourth_secret_word function. If you get the word wrong, this function will error.

    Args:
        prev_secret_word (str): The secret word retrieved from calling second_secret_word.
    """
    if prev_secret_word != "4rwp2b4gxq":
        raise RuntimeError(f'Expected secret "4rwp2b4gxq", got {prev_secret_word}')

    return "hj2hwibbqm"


def fourth_secret_word(prev_secret_word: str):
    """
    Call this to retrieve the last secret word, which you will need to output in a send_message later. If you get the word wrong, this function will error.

    Args:
        prev_secret_word (str): The secret word retrieved from calling third_secret_word.
    """
    if prev_secret_word != "hj2hwibbqm":
        raise RuntimeError(f"Expected secret {'hj2hwibbqm'}, got {prev_secret_word}")

    return "banana"


def flip_coin():
    """
    Call this to retrieve the password to the secret word, which you will need to output in a send_message later.
    If it returns an empty string, try flipping again!

    Returns:
        str: The password or an empty string
    """
    import random

    # Flip a coin with 50% chance
    if random.random() < 0.5:
        return ""
    return "hj2hwibbqm"


def flip_coin_hard():
    """
    Call this to retrieve the password to the secret word, which you will need to output in a send_message later.
    If it returns an empty string, try flipping again!

    Returns:
        str: The password or an empty string
    """
    import random

    # Flip a coin with 50% chance
    result = random.random()
    if result < 0.5:
        return ""
    if result < 0.75:
        return "START_OVER"
    return "hj2hwibbqm"


def can_play_game():
    """
    Call this to start the tool chain.
    """
    import random

    return random.random() < 0.5


def return_none():
    """
    Really simple function
    """
    return None


def auto_error():
    """
    If you call this function, it will throw an error automatically.
    """
    raise RuntimeError("This should never be called.")


@pytest.mark.timeout(60)  # Sets a 60-second timeout for the test since this could loop infinitely
def test_single_path_agent_tool_call_graph(mock_e2b_api_key_none):
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Add tools
    t1 = client.create_or_update_tool(first_secret_word)
    t2 = client.create_or_update_tool(second_secret_word)
    t3 = client.create_or_update_tool(third_secret_word)
    t4 = client.create_or_update_tool(fourth_secret_word)
    t_err = client.create_or_update_tool(auto_error)
    tools = [t1, t2, t3, t4, t_err]

    # Make tool rules
    tool_rules = [
        InitToolRule(tool_name="first_secret_word"),
        ChildToolRule(tool_name="first_secret_word", children=["second_secret_word"]),
        ChildToolRule(tool_name="second_secret_word", children=["third_secret_word"]),
        ChildToolRule(tool_name="third_secret_word", children=["fourth_secret_word"]),
        ChildToolRule(tool_name="fourth_secret_word", children=["send_message"]),
        TerminalToolRule(tool_name="send_message"),
    ]

    # Make agent state
    agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)
    response = client.user_message(agent_id=agent_state.id, message="What is the fourth secret word?")

    # Make checks
    assert_sanity_checks(response)

    # Assert the tools were called
    assert_invoked_function_call(response.messages, "first_secret_word")
    assert_invoked_function_call(response.messages, "second_secret_word")
    assert_invoked_function_call(response.messages, "third_secret_word")
    assert_invoked_function_call(response.messages, "fourth_secret_word")

    # Check ordering of tool calls
    tool_names = [t.name for t in [t1, t2, t3, t4]]
    tool_names += ["send_message"]
    for m in response.messages:
        if isinstance(m, ToolCallMessage):
            # Check that it's equal to the first one
            assert m.tool_call.name == tool_names[0]

            # Pop out first one
            tool_names = tool_names[1:]

    # Check final send message contains "done"
    assert_invoked_send_message_with_keyword(response.messages, "banana")

    print(f"Got successful response from client: \n\n{response}")
    cleanup(client=client, agent_uuid=agent_uuid)


def test_check_tool_rules_with_different_models(mock_e2b_api_key_none):
    """Test that tool rules are properly checked for different model configurations."""
    client = create_client()

    config_files = [
        "tests/configs/llm_model_configs/claude-3-5-sonnet.json",
        "tests/configs/llm_model_configs/openai-gpt-3.5-turbo.json",
        "tests/configs/llm_model_configs/openai-gpt-4o.json",
    ]

    # Create two test tools
    t1_name = "first_secret_word"
    t2_name = "second_secret_word"
    t1 = client.create_or_update_tool(first_secret_word)
    t2 = client.create_or_update_tool(second_secret_word)
    tool_rules = [InitToolRule(tool_name=t1_name), InitToolRule(tool_name=t2_name)]
    tools = [t1, t2]

    for config_file in config_files:
        # Setup tools
        agent_uuid = str(uuid.uuid4())

        if "gpt-4o" in config_file:
            # Structured output model (should work with multiple init tools)
            agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)
            assert agent_state is not None
        else:
            # Non-structured output model (should raise error with multiple init tools)
            with pytest.raises(ValueError, match="Multiple initial tools are not supported for non-structured models"):
                setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)

        # Cleanup
        cleanup(client=client, agent_uuid=agent_uuid)

    # Create tool rule with single initial tool
    t3_name = "third_secret_word"
    t3 = client.create_or_update_tool(third_secret_word)
    tool_rules = [InitToolRule(tool_name=t3_name)]
    tools = [t3]
    for config_file in config_files:
        agent_uuid = str(uuid.uuid4())

        # Structured output model (should work with single init tool)
        agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)
        assert agent_state is not None

        cleanup(client=client, agent_uuid=agent_uuid)


def test_claude_initial_tool_rule_enforced(mock_e2b_api_key_none):
    """Test that the initial tool rule is enforced for the first message."""
    client = create_client()

    # Create tool rules that require tool_a to be called first
    t1_name = "first_secret_word"
    t2_name = "second_secret_word"
    t1 = client.create_or_update_tool(first_secret_word)
    t2 = client.create_or_update_tool(second_secret_word)
    tool_rules = [
        InitToolRule(tool_name=t1_name),
        ChildToolRule(tool_name=t1_name, children=[t2_name]),
        TerminalToolRule(tool_name=t2_name),
    ]
    tools = [t1, t2]

    # Make agent state
    anthropic_config_file = "tests/configs/llm_model_configs/claude-3-5-sonnet.json"
    for i in range(3):
        agent_uuid = str(uuid.uuid4())
        agent_state = setup_agent(
            client, anthropic_config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules
        )
        response = client.user_message(agent_id=agent_state.id, message="What is the second secret word?")

        assert_sanity_checks(response)
        messages = response.messages

        assert_invoked_function_call(messages, "first_secret_word")
        assert_invoked_function_call(messages, "second_secret_word")

        tool_names = [t.name for t in [t1, t2]]
        tool_names += ["send_message"]
        for m in messages:
            if isinstance(m, ToolCallMessage):
                # Check that it's equal to the first one
                assert m.tool_call.name == tool_names[0]

                # Pop out first one
                tool_names = tool_names[1:]

        print(f"Passed iteration {i}")
        cleanup(client=client, agent_uuid=agent_uuid)

        # Implement exponential backoff with initial time of 10 seconds
        if i < 2:
            backoff_time = 10 * (2**i)
            time.sleep(backoff_time)


@pytest.mark.timeout(60)  # Sets a 60-second timeout for the test since this could loop infinitely
def test_agent_no_structured_output_with_one_child_tool(mock_e2b_api_key_none):
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    send_message = client.server.tool_manager.get_tool_by_name(tool_name="send_message", actor=client.user)
    archival_memory_search = client.server.tool_manager.get_tool_by_name(tool_name="archival_memory_search", actor=client.user)
    archival_memory_insert = client.server.tool_manager.get_tool_by_name(tool_name="archival_memory_insert", actor=client.user)

    # Make tool rules
    tool_rules = [
        InitToolRule(tool_name="archival_memory_search"),
        ChildToolRule(tool_name="archival_memory_search", children=["archival_memory_insert"]),
        ChildToolRule(tool_name="archival_memory_insert", children=["send_message"]),
        TerminalToolRule(tool_name="send_message"),
    ]
    tools = [send_message, archival_memory_search, archival_memory_insert]

    config_files = [
        "tests/configs/llm_model_configs/claude-3-5-sonnet.json",
        "tests/configs/llm_model_configs/openai-gpt-4o.json",
    ]

    for config in config_files:
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                agent_state = setup_agent(client, config, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)
                response = client.user_message(agent_id=agent_state.id, message="hi. run archival memory search")

                # Make checks
                assert_sanity_checks(response)

                # Assert the tools were called
                assert_invoked_function_call(response.messages, "archival_memory_search")
                assert_invoked_function_call(response.messages, "archival_memory_insert")
                assert_invoked_function_call(response.messages, "send_message")

                # Check ordering of tool calls
                tool_names = [t.name for t in [archival_memory_search, archival_memory_insert, send_message]]
                for m in response.messages:
                    if isinstance(m, ToolCallMessage):
                        # Check that it's equal to the first one
                        assert m.tool_call.name == tool_names[0]

                        # Pop out first one
                        tool_names = tool_names[1:]

                print(f"Got successful response from client: \n\n{response}")
                break  # Test passed, exit retry loop

            except AssertionError as e:
                last_error = e
                print(f"Attempt {attempt + 1} failed, retrying..." if attempt < max_retries - 1 else f"All {max_retries} attempts failed")
                cleanup(client=client, agent_uuid=agent_uuid)
                continue

        if last_error and attempt == max_retries - 1:
            raise last_error  # Re-raise the last error if all retries failed

        cleanup(client=client, agent_uuid=agent_uuid)


@pytest.mark.timeout(60)  # Sets a 60-second timeout for the test since this could loop infinitely
def test_agent_conditional_tool_easy(mock_e2b_api_key_none):
    """
    Test the agent with a conditional tool that has a child tool.

                Tool Flow:

                     -------
                    |       |
                    |       v
                     -- flip_coin
                            |
                            v
                    reveal_secret_word
    """

    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    coin_flip_name = "flip_coin"
    secret_word_tool = "fourth_secret_word"
    flip_coin_tool = client.create_or_update_tool(flip_coin)
    reveal_secret = client.create_or_update_tool(fourth_secret_word)

    # Make tool rules
    tool_rules = [
        InitToolRule(tool_name=coin_flip_name),
        ConditionalToolRule(
            tool_name=coin_flip_name,
            default_child=coin_flip_name,
            child_output_mapping={
                "hj2hwibbqm": secret_word_tool,
            },
        ),
        TerminalToolRule(tool_name=secret_word_tool),
    ]
    tools = [flip_coin_tool, reveal_secret]

    config_file = "tests/configs/llm_model_configs/claude-3-5-sonnet.json"
    agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)
    response = client.user_message(agent_id=agent_state.id, message="flip a coin until you get the secret word")

    # Make checks
    assert_sanity_checks(response)

    # Assert the tools were called
    assert_invoked_function_call(response.messages, "flip_coin")
    assert_invoked_function_call(response.messages, "fourth_secret_word")

    # Check ordering of tool calls
    found_secret_word = False
    for m in response.messages:
        if isinstance(m, ToolCallMessage):
            if m.tool_call.name == secret_word_tool:
                # Should be the last tool call
                found_secret_word = True
            else:
                # Before finding secret_word, only flip_coin should be called
                assert m.tool_call.name == coin_flip_name
                assert not found_secret_word

    # Ensure we found the secret word exactly once
    assert found_secret_word

    print(f"Got successful response from client: \n\n{response}")
    cleanup(client=client, agent_uuid=agent_uuid)


@pytest.mark.timeout(90)  # Longer timeout since this test has more steps
def test_agent_conditional_tool_hard(mock_e2b_api_key_none):
    """
    Test the agent with a complex conditional tool graph

                Tool Flow:

                can_play_game <---+
                     |           |
                     v           |
                  flip_coin -----+
                     |
                     v
             fourth_secret_word
    """
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Create tools
    play_game = "can_play_game"
    coin_flip_name = "flip_coin_hard"
    final_tool = "fourth_secret_word"
    play_game_tool = client.create_or_update_tool(can_play_game)
    flip_coin_tool = client.create_or_update_tool(flip_coin_hard)
    reveal_secret = client.create_or_update_tool(fourth_secret_word)

    # Make tool rules - chain them together with conditional rules
    tool_rules = [
        InitToolRule(tool_name=play_game),
        ConditionalToolRule(
            tool_name=play_game,
            default_child=play_game,  # Keep trying if we can't play
            child_output_mapping={True: coin_flip_name},  # Only allow access when can_play_game returns True
        ),
        ConditionalToolRule(
            tool_name=coin_flip_name, default_child=coin_flip_name, child_output_mapping={"hj2hwibbqm": final_tool, "START_OVER": play_game}
        ),
        TerminalToolRule(tool_name=final_tool),
    ]

    # Setup agent with all tools
    tools = [play_game_tool, flip_coin_tool, reveal_secret]
    config_file = "tests/configs/llm_model_configs/claude-3-5-sonnet.json"
    agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)

    # Ask agent to try to get all secret words
    response = client.user_message(agent_id=agent_state.id, message="hi")

    # Make checks
    assert_sanity_checks(response)

    # Assert all tools were called
    assert_invoked_function_call(response.messages, play_game)
    assert_invoked_function_call(response.messages, final_tool)

    # Check ordering of tool calls
    found_words = []
    for m in response.messages:
        if isinstance(m, ToolCallMessage):
            name = m.tool_call.name
            if name in [play_game, coin_flip_name]:
                # Before finding secret_word, only can_play_game and flip_coin should be called
                assert name in [play_game, coin_flip_name]
            else:
                # Should find secret words in order
                expected_word = final_tool
                assert name == expected_word, f"Found {name} but expected {expected_word}"
                found_words.append(name)

    # Ensure we found all secret words in order
    assert found_words == [final_tool]

    print(f"Got successful response from client: \n\n{response}")
    cleanup(client=client, agent_uuid=agent_uuid)


@pytest.mark.timeout(60)
def test_agent_conditional_tool_without_default_child(mock_e2b_api_key_none):
    """
    Test the agent with a conditional tool that allows any child tool to be called if a function returns None.

                Tool Flow:

                return_none
                     |
                     v
                any tool...  <-- When output doesn't match mapping, agent can call any tool
    """
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Create tools - we'll make several available to the agent
    tool_name = "return_none"

    tool = client.create_or_update_tool(return_none)
    secret_word = client.create_or_update_tool(first_secret_word)

    # Make tool rules - only map one output, let others be free choice
    tool_rules = [
        InitToolRule(tool_name=tool_name),
        ConditionalToolRule(
            tool_name=tool_name,
            default_child=None,  # Allow any tool to be called if output doesn't match
            child_output_mapping={"anything but none": "first_secret_word"},
        ),
    ]
    tools = [tool, secret_word]

    # Setup agent with all tools
    agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)

    # Ask agent to try different tools based on the game output
    response = client.user_message(agent_id=agent_state.id, message="call a function, any function. then call send_message")

    # Make checks
    assert_sanity_checks(response)

    # Assert return_none was called
    assert_invoked_function_call(response.messages, tool_name)

    # Assert any base function called afterward
    found_any_tool = False
    found_return_none = False
    for m in response.messages:
        if isinstance(m, ToolCallMessage):
            if m.tool_call.name == tool_name:
                found_return_none = True
            elif found_return_none and m.tool_call.name:
                found_any_tool = True
                break

    assert found_any_tool, "Should have called any tool after return_none"

    print(f"Got successful response from client: \n\n{response}")
    cleanup(client=client, agent_uuid=agent_uuid)


@pytest.mark.timeout(60)
def test_agent_reload_remembers_function_response(mock_e2b_api_key_none):
    """
    Test that when an agent is reloaded, it remembers the last function response for conditional tool chaining.

                Tool Flow:

                flip_coin
                     |
                     v
            fourth_secret_word  <-- Should remember coin flip result after reload
    """
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Create tools
    flip_coin_name = "flip_coin"
    secret_word = "fourth_secret_word"
    flip_coin_tool = client.create_or_update_tool(flip_coin)
    secret_word_tool = client.create_or_update_tool(fourth_secret_word)

    # Make tool rules - map coin flip to fourth_secret_word
    tool_rules = [
        InitToolRule(tool_name=flip_coin_name),
        ConditionalToolRule(
            tool_name=flip_coin_name,
            default_child=flip_coin_name,  # Allow any tool to be called if output doesn't match
            child_output_mapping={"hj2hwibbqm": secret_word},
        ),
        TerminalToolRule(tool_name=secret_word),
    ]
    tools = [flip_coin_tool, secret_word_tool]

    # Setup initial agent
    agent_state = setup_agent(client, config_file, agent_uuid=agent_uuid, tool_ids=[t.id for t in tools], tool_rules=tool_rules)

    # Call flip_coin first
    response = client.user_message(agent_id=agent_state.id, message="flip a coin")
    assert_invoked_function_call(response.messages, flip_coin_name)
    assert_invoked_function_call(response.messages, secret_word)
    found_fourth_secret = False
    for m in response.messages:
        if isinstance(m, ToolCallMessage) and m.tool_call.name == secret_word:
            found_fourth_secret = True
            break

    assert found_fourth_secret, "Reloaded agent should remember coin flip result and call fourth_secret_word if True"

    # Reload the agent
    reloaded_agent = client.server.load_agent(agent_id=agent_state.id, actor=client.user)
    assert reloaded_agent.last_function_response is not None

    print(f"Got successful response from client: \n\n{response}")
    cleanup(client=client, agent_uuid=agent_uuid)


@pytest.mark.timeout(60)  # Sets a 60-second timeout for the test since this could loop infinitely
def test_simple_tool_rule(mock_e2b_api_key_none):
    """
    Test a simple tool rule where fourth_secret_word must be called after flip_coin.

    Tool Flow:
        flip_coin
           |
           v
    fourth_secret_word
    """
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Create tools
    flip_coin_name = "flip_coin"
    secret_word = "fourth_secret_word"
    random_tool = "can_play_game"
    flip_coin_tool = client.create_or_update_tool(flip_coin)
    secret_word_tool = client.create_or_update_tool(fourth_secret_word)
    another_secret_word_tool = client.create_or_update_tool(first_secret_word)
    random_tool = client.create_or_update_tool(can_play_game)
    tools = [flip_coin_tool, secret_word_tool, another_secret_word_tool, random_tool]

    # Create tool rule: after flip_coin, must call fourth_secret_word
    tool_rule = ConditionalToolRule(
        tool_name=flip_coin_name,
        default_child=secret_word,
        child_output_mapping={"*": secret_word},
    )

    # Set up agent with the tool rule
    agent_state = setup_agent(
        client, config_file, agent_uuid, tool_rules=[tool_rule], tool_ids=[t.id for t in tools], include_base_tools=False
    )

    # Start conversation
    response = client.user_message(agent_id=agent_state.id, message="Help me test the tools.")

    # Verify the tool calls
    tool_calls = [msg for msg in response.messages if isinstance(msg, ToolCallMessage)]
    assert len(tool_calls) >= 2  # Should have at least flip_coin and fourth_secret_word calls
    assert_invoked_function_call(response.messages, flip_coin_name)
    assert_invoked_function_call(response.messages, secret_word)

    # Find the flip_coin call
    flip_coin_call = next((call for call in tool_calls if call.tool_call.name == "flip_coin"), None)

    # Verify that fourth_secret_word was called after flip_coin
    flip_coin_call_index = tool_calls.index(flip_coin_call)
    assert tool_calls[flip_coin_call_index + 1].tool_call.name == secret_word, "Fourth secret word should be called after flip_coin"

    cleanup(client, agent_uuid=agent_state.id)


def test_init_tool_rule_always_fails_one_tool():
    """
    Test an init tool rule that always fails when called. The agent has only one tool available.

    Once that tool fails and the agent removes that tool, the agent should have 0 tools available.

    This means that the agent should return from `step` early.
    """
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Create tools
    bad_tool = client.create_or_update_tool(auto_error)

    # Create tool rule: InitToolRule
    tool_rule = InitToolRule(
        tool_name=bad_tool.name,
    )

    # Set up agent with the tool rule
    claude_config = "tests/configs/llm_model_configs/claude-3-5-sonnet.json"
    agent_state = setup_agent(client, claude_config, agent_uuid, tool_rules=[tool_rule], tool_ids=[bad_tool.id], include_base_tools=False)

    # Start conversation
    response = client.user_message(agent_id=agent_state.id, message="blah blah blah")

    # Verify the tool calls
    tool_calls = [msg for msg in response.messages if isinstance(msg, ToolCallMessage)]
    assert len(tool_calls) >= 1  # Should have at least flip_coin and fourth_secret_word calls
    assert_invoked_function_call(response.messages, bad_tool.name)


def test_init_tool_rule_always_fails_multiple_tools():
    """
    Test an init tool rule that always fails when called. The agent has only 1+ tools available.
    Once that tool fails and the agent removes that tool, the agent should have other tools available.
    """
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    # Create tools
    bad_tool = client.create_or_update_tool(auto_error)

    # Create tool rule: InitToolRule
    tool_rule = InitToolRule(
        tool_name=bad_tool.name,
    )

    # Set up agent with the tool rule
    claude_config = "tests/configs/llm_model_configs/claude-3-5-sonnet.json"
    agent_state = setup_agent(client, claude_config, agent_uuid, tool_rules=[tool_rule], tool_ids=[bad_tool.id], include_base_tools=True)

    # Start conversation
    response = client.user_message(agent_id=agent_state.id, message="blah blah blah")

    # Verify the tool calls
    tool_calls = [msg for msg in response.messages if isinstance(msg, ToolCallMessage)]
    assert len(tool_calls) >= 1  # Should have at least flip_coin and fourth_secret_word calls
    assert_invoked_function_call(response.messages, bad_tool.name)


def test_continue_tool_rule():
    """Test the continue tool rule by forcing the send_message tool to continue"""
    client = create_client()
    cleanup(client=client, agent_uuid=agent_uuid)

    continue_tool_rule = ContinueToolRule(
        tool_name="send_message",
    )
    terminal_tool_rule = TerminalToolRule(
        tool_name="core_memory_append",
    )
    rules = [continue_tool_rule, terminal_tool_rule]

    core_memory_append_tool = client.get_tool_id("core_memory_append")
    send_message_tool = client.get_tool_id("send_message")

    # Set up agent with the tool rule
    claude_config = "tests/configs/llm_model_configs/claude-3-5-sonnet.json"
    agent_state = setup_agent(
        client,
        claude_config,
        agent_uuid,
        tool_rules=rules,
        tool_ids=[core_memory_append_tool, send_message_tool],
        include_base_tools=False,
        include_base_tool_rules=False,
    )

    # Start conversation
    response = client.user_message(agent_id=agent_state.id, message="blah blah blah")

    # Verify the tool calls
    tool_calls = [msg for msg in response.messages if isinstance(msg, ToolCallMessage)]
    assert len(tool_calls) >= 1
    assert_invoked_function_call(response.messages, "send_message")
    assert_invoked_function_call(response.messages, "core_memory_append")

    # ensure send_message called before core_memory_append
    send_message_call_index = None
    core_memory_append_call_index = None
    for i, call in enumerate(tool_calls):
        if call.tool_call.name == "send_message":
            send_message_call_index = i
        if call.tool_call.name == "core_memory_append":
            core_memory_append_call_index = i
    assert send_message_call_index < core_memory_append_call_index, "send_message should have been called before core_memory_append"
