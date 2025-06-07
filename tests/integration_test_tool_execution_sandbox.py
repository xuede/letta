import secrets
import string
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete

from letta.config import LettaConfig
from letta.functions.function_sets.base import core_memory_append, core_memory_replace
from letta.orm.sandbox_config import SandboxConfig, SandboxEnvironmentVariable
from letta.schemas.agent import AgentState, CreateAgent
from letta.schemas.block import CreateBlock
from letta.schemas.environment_variables import AgentEnvironmentVariable, SandboxEnvironmentVariableCreate
from letta.schemas.organization import Organization
from letta.schemas.sandbox_config import E2BSandboxConfig, LocalSandboxConfig, PipRequirement, SandboxConfigCreate, SandboxConfigUpdate
from letta.schemas.user import User
from letta.server.server import SyncServer
from letta.services.organization_manager import OrganizationManager
from letta.services.sandbox_config_manager import SandboxConfigManager
from letta.services.tool_executor.tool_execution_sandbox import ToolExecutionSandbox
from letta.services.tool_manager import ToolManager
from letta.services.user_manager import UserManager
from tests.helpers.utils import create_tool_from_func

# Constants
namespace = uuid.NAMESPACE_DNS
org_name = str(uuid.uuid5(namespace, "test-tool-execution-sandbox-org"))
user_name = str(uuid.uuid5(namespace, "test-tool-execution-sandbox-user"))


# Fixtures
@pytest.fixture(scope="module")
def server():
    """
    Creates a SyncServer instance for testing.

    Loads and saves config to ensure proper initialization.
    """
    config = LettaConfig.load()

    config.save()

    server = SyncServer(init_with_default_org_and_user=True)
    yield server


@pytest.fixture(autouse=True)
def clear_tables():
    """Fixture to clear the organization table before each test."""
    from letta.server.db import db_context

    with db_context() as session:
        session.execute(delete(SandboxEnvironmentVariable))
        session.execute(delete(SandboxConfig))
        session.commit()  # Commit the deletion


@pytest.fixture
def test_organization():
    """Fixture to create and return the default organization."""
    org = OrganizationManager().create_organization(Organization(name=org_name))
    yield org


@pytest.fixture
def test_user(test_organization):
    """Fixture to create and return the default user within the default organization."""
    user = UserManager().create_user(User(name=user_name, organization_id=test_organization.id))
    yield user


@pytest.fixture
def add_integers_tool(test_user):
    def add(x: int, y: int) -> int:
        """
        Simple function that adds two integers.

        Parameters:
            x (int): The first integer to add.
            y (int): The second integer to add.

        Returns:
            int: The result of adding x and y.
        """
        return x + y

    tool = create_tool_from_func(add)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def cowsay_tool(test_user):
    # This defines a tool for a package we definitely do NOT have in letta
    # If this test passes, that means the tool was correctly executed in a separate Python environment
    def cowsay() -> str:
        """
        Simple function that uses the cowsay package to print out the secret word env variable.

        Returns:
            str: The cowsay ASCII art.
        """
        import os

        import cowsay

        cowsay.cow(os.getenv("secret_word"))

    tool = create_tool_from_func(cowsay)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def get_env_tool(test_user):
    def get_env() -> str:
        """
        Simple function that returns the secret word env variable.

        Returns:
            str: The secret word
        """
        import os

        secret_word = os.getenv("secret_word")
        print(secret_word)
        return secret_word

    tool = create_tool_from_func(get_env)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def get_warning_tool(test_user):
    def warn_hello_world() -> str:
        """
        Simple function that warns hello world.

        Returns:
            str: hello world
        """
        import warnings

        msg = "Hello World"
        warnings.warn(msg)
        return msg

    tool = create_tool_from_func(warn_hello_world)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def always_err_tool(test_user):
    def error() -> str:
        """
        Simple function that errors

        Returns:
            str: not important
        """
        # Raise a unusual error so we know it's from this function
        print("Going to error now")
        raise ZeroDivisionError("This is an intentionally weird division!")

    tool = create_tool_from_func(error)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def list_tool(test_user):
    def create_list():
        """Simple function that returns a list"""

        return [1] * 5

    tool = create_tool_from_func(create_list)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def clear_core_memory_tool(test_user):
    def clear_memory(agent_state: "AgentState"):
        """Clear the core memory"""
        agent_state.memory.get_block("human").value = ""
        agent_state.memory.get_block("persona").value = ""

    tool = create_tool_from_func(clear_memory)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def external_codebase_tool(test_user):
    from tests.test_tool_sandbox.restaurant_management_system.adjust_menu_prices import adjust_menu_prices

    tool = create_tool_from_func(adjust_menu_prices)
    tool = ToolManager().create_or_update_tool(tool, test_user)
    yield tool


@pytest.fixture
def agent_state(server):
    actor = server.user_manager.get_user_or_default()
    agent_state = server.create_agent(
        CreateAgent(
            memory_blocks=[
                CreateBlock(
                    label="human",
                    value="username: sarah",
                ),
                CreateBlock(
                    label="persona",
                    value="This is the persona",
                ),
            ],
            include_base_tools=True,
            model="openai/gpt-4o-mini",
            tags=["test_agents"],
            embedding="letta/letta-free",
        ),
        actor=actor,
    )
    agent_state.tool_rules = []
    yield agent_state


@pytest.fixture
def custom_test_sandbox_config(test_user):
    """
    Fixture to create a consistent local sandbox configuration for tests.

    Args:
        test_user: The test user to be used for creating the sandbox configuration.

    Returns:
        A tuple containing the SandboxConfigManager and the created sandbox configuration.
    """
    # Create the SandboxConfigManager
    manager = SandboxConfigManager()

    # Set the sandbox to be within the external codebase path and use a venv
    external_codebase_path = str(Path(__file__).parent / "test_tool_sandbox" / "restaurant_management_system")
    # tqdm is used in this codebase, but NOT in the requirements.txt, this tests that we can successfully install pip requirements
    local_sandbox_config = LocalSandboxConfig(
        sandbox_dir=external_codebase_path, use_venv=True, pip_requirements=[PipRequirement(name="tqdm")]
    )

    # Create the sandbox configuration
    config_create = SandboxConfigCreate(config=local_sandbox_config.model_dump())

    # Create or update the sandbox configuration
    manager.create_or_update_sandbox_config(sandbox_config_create=config_create, actor=test_user)

    return manager, local_sandbox_config


# Tool-specific fixtures
@pytest.fixture
def core_memory_tools(test_user):
    """Create all base tools for testing."""
    tools = {}
    for func in [
        core_memory_replace,
        core_memory_append,
    ]:
        tool = create_tool_from_func(func)
        tool = ToolManager().create_or_update_tool(tool, test_user)
        tools[func.__name__] = tool
    yield tools


# Local sandbox tests


@pytest.mark.local_sandbox
def test_local_sandbox_default(disable_e2b_api_key, add_integers_tool, test_user):
    args = {"x": 10, "y": 5}

    # Mock and assert correct pathway was invoked
    with patch.object(ToolExecutionSandbox, "run_local_dir_sandbox") as mock_run_local_dir_sandbox:
        sandbox = ToolExecutionSandbox(add_integers_tool.name, args, user=test_user)
        sandbox.run()
        mock_run_local_dir_sandbox.assert_called_once()

    # Run again to get actual response
    sandbox = ToolExecutionSandbox(add_integers_tool.name, args, user=test_user)
    result = sandbox.run()
    assert result.func_return == args["x"] + args["y"]


@pytest.mark.local_sandbox
def test_local_sandbox_stateful_tool(disable_e2b_api_key, clear_core_memory_tool, test_user, agent_state):
    args = {}
    # Run again to get actual response
    sandbox = ToolExecutionSandbox(clear_core_memory_tool.name, args, user=test_user)
    result = sandbox.run(agent_state=agent_state)
    assert result.agent_state.memory.get_block("human").value == ""
    assert result.agent_state.memory.get_block("persona").value == ""
    assert result.func_return is None


@pytest.mark.local_sandbox
def test_local_sandbox_with_list_rv(disable_e2b_api_key, list_tool, test_user):
    sandbox = ToolExecutionSandbox(list_tool.name, {}, user=test_user)
    result = sandbox.run()
    assert len(result.func_return) == 5


@pytest.mark.local_sandbox
def test_local_sandbox_env(disable_e2b_api_key, get_env_tool, test_user):
    manager = SandboxConfigManager()

    # Make a custom local sandbox config
    sandbox_dir = str(Path(__file__).parent / "test_tool_sandbox")
    config_create = SandboxConfigCreate(config=LocalSandboxConfig(sandbox_dir=sandbox_dir).model_dump())
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Make a environment variable with a long random string
    key = "secret_word"
    long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    # Create tool and args
    args = {}

    # Run the custom sandbox
    sandbox = ToolExecutionSandbox(get_env_tool.name, args, user=test_user)
    result = sandbox.run()

    assert long_random_string in result.func_return


@pytest.mark.local_sandbox
def test_local_sandbox_per_agent_env(disable_e2b_api_key, get_env_tool, agent_state, test_user):
    manager = SandboxConfigManager()
    key = "secret_word"

    # Make a custom local sandbox config
    sandbox_dir = str(Path(__file__).parent / "test_tool_sandbox")
    config_create = SandboxConfigCreate(config=LocalSandboxConfig(sandbox_dir=sandbox_dir).model_dump())
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Make a environment variable with a long random string
    # Note: This has an overlapping key with agent state's environment variables
    # We expect that the agent's env var supersedes this
    wrong_long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=wrong_long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    # Make a environment variable with a long random string and put into agent state
    correct_long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    agent_state.tool_exec_environment_variables = [
        AgentEnvironmentVariable(key=key, value=correct_long_random_string, agent_id=agent_state.id)
    ]

    # Create tool and args
    args = {}

    # Run the custom sandbox
    sandbox = ToolExecutionSandbox(get_env_tool.name, args, user=test_user)
    result = sandbox.run(agent_state=agent_state)

    assert wrong_long_random_string not in result.func_return
    assert correct_long_random_string in result.func_return


@pytest.mark.local_sandbox
def test_local_sandbox_external_codebase_with_venv(disable_e2b_api_key, custom_test_sandbox_config, external_codebase_tool, test_user):
    # Set the args
    args = {"percentage": 10}

    # Run again to get actual response
    sandbox = ToolExecutionSandbox(external_codebase_tool.name, args, user=test_user)
    result = sandbox.run()

    # Assert that the function return is correct
    assert result.func_return == "Price Adjustments:\nBurger: $8.99 -> $9.89\nFries: $2.99 -> $3.29\nSoda: $1.99 -> $2.19"
    assert "Hello World" in result.stdout[0]


@pytest.mark.local_sandbox
def test_local_sandbox_with_venv_and_warnings_does_not_error(disable_e2b_api_key, custom_test_sandbox_config, get_warning_tool, test_user):
    sandbox = ToolExecutionSandbox(get_warning_tool.name, {}, user=test_user)
    result = sandbox.run()
    assert result.func_return == "Hello World"


@pytest.mark.e2b_sandbox
def test_local_sandbox_with_venv_errors(disable_e2b_api_key, custom_test_sandbox_config, always_err_tool, test_user):
    sandbox = ToolExecutionSandbox(always_err_tool.name, {}, user=test_user)

    # run the sandbox
    result = sandbox.run()
    assert len(result.stdout) != 0, "stdout not empty"
    assert "error" in result.stdout[0], "stdout contains printed string"
    assert len(result.stderr) != 0, "stderr not empty"
    assert "ZeroDivisionError: This is an intentionally weird division!" in result.stderr[0], "stderr contains expected error"


@pytest.mark.e2b_sandbox
def test_local_sandbox_with_venv_pip_installs_basic(disable_e2b_api_key, cowsay_tool, test_user):
    manager = SandboxConfigManager()
    config_create = SandboxConfigCreate(
        config=LocalSandboxConfig(use_venv=True, pip_requirements=[PipRequirement(name="cowsay")]).model_dump()
    )
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Add an environment variable
    key = "secret_word"
    long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    sandbox = ToolExecutionSandbox(cowsay_tool.name, {}, user=test_user, force_recreate_venv=True)
    result = sandbox.run()
    assert long_random_string in result.stdout[0]


@pytest.mark.e2b_sandbox
def test_local_sandbox_with_venv_pip_installs_with_update(disable_e2b_api_key, cowsay_tool, test_user):
    manager = SandboxConfigManager()
    config_create = SandboxConfigCreate(config=LocalSandboxConfig(use_venv=True).model_dump())
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Add an environment variable
    key = "secret_word"
    long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    sandbox = ToolExecutionSandbox(cowsay_tool.name, {}, user=test_user, force_recreate_venv=True)
    result = sandbox.run()

    # Check that this should error
    assert len(result.stdout) == 0
    error_message = "No module named 'cowsay'"
    assert error_message in result.stderr[0]

    # Now update the SandboxConfig
    config_create = SandboxConfigCreate(
        config=LocalSandboxConfig(use_venv=True, pip_requirements=[PipRequirement(name="cowsay")]).model_dump()
    )
    manager.create_or_update_sandbox_config(config_create, test_user)

    # Run it again WITHOUT force recreating the venv
    sandbox = ToolExecutionSandbox(cowsay_tool.name, {}, user=test_user, force_recreate_venv=False)
    result = sandbox.run()
    assert long_random_string in result.stdout[0]


# E2B sandbox tests


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_default(check_e2b_key_is_set, add_integers_tool, test_user):
    args = {"x": 10, "y": 5}

    # Mock and assert correct pathway was invoked
    with patch.object(ToolExecutionSandbox, "run_e2b_sandbox") as mock_run_local_dir_sandbox:
        sandbox = ToolExecutionSandbox(add_integers_tool.name, args, user=test_user)
        sandbox.run()
        mock_run_local_dir_sandbox.assert_called_once()

    # Run again to get actual response
    sandbox = ToolExecutionSandbox(add_integers_tool.name, args, user=test_user)
    result = sandbox.run()
    assert int(result.func_return) == args["x"] + args["y"]


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_pip_installs(check_e2b_key_is_set, cowsay_tool, test_user):
    manager = SandboxConfigManager()
    config_create = SandboxConfigCreate(config=E2BSandboxConfig(pip_requirements=["cowsay"]).model_dump())
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Add an environment variable
    key = "secret_word"
    long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    sandbox = ToolExecutionSandbox(cowsay_tool.name, {}, user=test_user)
    result = sandbox.run()
    assert long_random_string in result.stdout[0]


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_reuses_same_sandbox(check_e2b_key_is_set, list_tool, test_user):
    sandbox = ToolExecutionSandbox(list_tool.name, {}, user=test_user)

    # Run the function once
    result = sandbox.run()
    old_config_fingerprint = result.sandbox_config_fingerprint

    # Run it again to ensure that there is still only one running sandbox
    result = sandbox.run()
    new_config_fingerprint = result.sandbox_config_fingerprint

    assert old_config_fingerprint == new_config_fingerprint


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_stateful_tool(check_e2b_key_is_set, clear_core_memory_tool, test_user, agent_state):
    sandbox = ToolExecutionSandbox(clear_core_memory_tool.name, {}, user=test_user)

    # run the sandbox
    result = sandbox.run(agent_state=agent_state)
    assert result.agent_state.memory.get_block("human").value == ""
    assert result.agent_state.memory.get_block("persona").value == ""
    assert result.func_return is None


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_inject_env_var_existing_sandbox(check_e2b_key_is_set, get_env_tool, test_user):
    manager = SandboxConfigManager()
    config_create = SandboxConfigCreate(config=E2BSandboxConfig().model_dump())
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Run the custom sandbox once, assert nothing returns because missing env variable
    sandbox = ToolExecutionSandbox(get_env_tool.name, {}, user=test_user)
    result = sandbox.run()
    # response should be None
    assert result.func_return is None

    # Add an environment variable
    key = "secret_word"
    long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    # Assert that the environment variable gets injected correctly, even when the sandbox is NOT refreshed
    sandbox = ToolExecutionSandbox(get_env_tool.name, {}, user=test_user)
    result = sandbox.run()
    assert long_random_string in result.func_return


# TODO: There is a near dupe of this test above for local sandbox - we should try to make it parameterized tests to minimize code bloat
@pytest.mark.e2b_sandbox
def test_e2b_sandbox_per_agent_env(check_e2b_key_is_set, get_env_tool, agent_state, test_user):
    manager = SandboxConfigManager()
    key = "secret_word"

    # Make a custom local sandbox config
    sandbox_dir = str(Path(__file__).parent / "test_tool_sandbox")
    config_create = SandboxConfigCreate(config=LocalSandboxConfig(sandbox_dir=sandbox_dir).model_dump())
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Make a environment variable with a long random string
    # Note: This has an overlapping key with agent state's environment variables
    # We expect that the agent's env var supersedes this
    wrong_long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    manager.create_sandbox_env_var(
        SandboxEnvironmentVariableCreate(key=key, value=wrong_long_random_string), sandbox_config_id=config.id, actor=test_user
    )

    # Make a environment variable with a long random string and put into agent state
    correct_long_random_string = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    agent_state.tool_exec_environment_variables = [
        AgentEnvironmentVariable(key=key, value=correct_long_random_string, agent_id=agent_state.id)
    ]

    # Create tool and args
    args = {}

    # Run the custom sandbox
    sandbox = ToolExecutionSandbox(get_env_tool.name, args, user=test_user)
    result = sandbox.run(agent_state=agent_state)

    assert wrong_long_random_string not in result.func_return
    assert correct_long_random_string in result.func_return


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_config_change_force_recreates_sandbox(check_e2b_key_is_set, list_tool, test_user):
    manager = SandboxConfigManager()
    old_timeout = 5 * 60
    new_timeout = 10 * 60

    # Make the config
    config_create = SandboxConfigCreate(config=E2BSandboxConfig(timeout=old_timeout))
    config = manager.create_or_update_sandbox_config(config_create, test_user)

    # Run the custom sandbox once, assert a failure gets returned because missing environment variable
    sandbox = ToolExecutionSandbox(list_tool.name, {}, user=test_user)
    result = sandbox.run()
    assert len(result.func_return) == 5
    old_config_fingerprint = result.sandbox_config_fingerprint

    # Change the config
    config_update = SandboxConfigUpdate(config=E2BSandboxConfig(timeout=new_timeout))
    config = manager.update_sandbox_config(config.id, config_update, test_user)

    # Run again
    result = ToolExecutionSandbox(list_tool.name, {}, user=test_user).run()
    new_config_fingerprint = result.sandbox_config_fingerprint
    assert config.fingerprint() == new_config_fingerprint

    # Assert the fingerprints are different
    assert old_config_fingerprint != new_config_fingerprint


@pytest.mark.e2b_sandbox
def test_e2b_sandbox_with_list_rv(check_e2b_key_is_set, list_tool, test_user):
    sandbox = ToolExecutionSandbox(list_tool.name, {}, user=test_user)
    result = sandbox.run()
    assert len(result.func_return) == 5
