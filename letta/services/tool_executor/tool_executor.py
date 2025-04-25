import math
import traceback
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from letta.constants import COMPOSIO_ENTITY_ENV_VAR_KEY, CORE_MEMORY_LINE_NUMBER_WARNING, RETRIEVAL_QUERY_DEFAULT_PAGE_SIZE
from letta.functions.ast_parsers import coerce_dict_args_by_annotations, get_function_annotations_from_source
from letta.functions.helpers import execute_composio_action, generate_composio_action_from_func_name
from letta.helpers.composio_helpers import get_composio_api_key
from letta.helpers.json_helpers import json_dumps
from letta.schemas.agent import AgentState
from letta.schemas.sandbox_config import SandboxConfig
from letta.schemas.tool import Tool
from letta.schemas.tool_execution_result import ToolExecutionResult
from letta.schemas.user import User
from letta.services.agent_manager import AgentManager
from letta.services.message_manager import MessageManager
from letta.services.passage_manager import PassageManager
from letta.services.tool_sandbox.e2b_sandbox import AsyncToolSandboxE2B
from letta.services.tool_sandbox.local_sandbox import AsyncToolSandboxLocal
from letta.settings import tool_settings
from letta.utils import get_friendly_error_msg


class ToolExecutor(ABC):
    """Abstract base class for tool executors."""

    @abstractmethod
    def execute(
        self,
        function_name: str,
        function_args: dict,
        agent_state: AgentState,
        tool: Tool,
        actor: User,
        sandbox_config: Optional[SandboxConfig] = None,
        sandbox_env_vars: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        """Execute the tool and return the result."""


class LettaCoreToolExecutor(ToolExecutor):
    """Executor for LETTA core tools with direct implementation of functions."""

    def execute(
        self,
        function_name: str,
        function_args: dict,
        agent_state: AgentState,
        tool: Tool,
        actor: User,
        sandbox_config: Optional[SandboxConfig] = None,
        sandbox_env_vars: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        # Map function names to method calls
        function_map = {
            "send_message": self.send_message,
            "conversation_search": self.conversation_search,
            "archival_memory_search": self.archival_memory_search,
            "archival_memory_insert": self.archival_memory_insert,
            "core_memory_append": self.core_memory_append,
            "core_memory_replace": self.core_memory_replace,
            "memory_replace": self.memory_replace,
            "memory_insert": self.memory_insert,
            "memory_rethink": self.memory_rethink,
            "memory_finish_edits": self.memory_finish_edits,
        }

        if function_name not in function_map:
            raise ValueError(f"Unknown function: {function_name}")

        # Execute the appropriate function
        function_args_copy = function_args.copy()  # Make a copy to avoid modifying the original
        function_response = function_map[function_name](agent_state, actor, **function_args_copy)
        return ToolExecutionResult(
            status="success",
            func_return=function_response,
        )

    def send_message(self, agent_state: AgentState, actor: User, message: str) -> Optional[str]:
        """
        Sends a message to the human user.

        Args:
            message (str): Message contents. All unicode (including emojis) are supported.

        Returns:
            Optional[str]: None is always returned as this function does not produce a response.
        """
        return "Sent message successfully."

    def conversation_search(self, agent_state: AgentState, actor: User, query: str, page: Optional[int] = 0) -> Optional[str]:
        """
        Search prior conversation history using case-insensitive string matching.

        Args:
            query (str): String to search for.
            page (int): Allows you to page through results. Only use on a follow-up query. Defaults to 0 (first page).

        Returns:
            str: Query result string
        """
        if page is None or (isinstance(page, str) and page.lower().strip() == "none"):
            page = 0
        try:
            page = int(page)
        except:
            raise ValueError(f"'page' argument must be an integer")

        count = RETRIEVAL_QUERY_DEFAULT_PAGE_SIZE
        messages = MessageManager().list_user_messages_for_agent(
            agent_id=agent_state.id,
            actor=actor,
            query_text=query,
            limit=count,
        )

        total = len(messages)
        num_pages = math.ceil(total / count) - 1  # 0 index

        if len(messages) == 0:
            results_str = f"No results found."
        else:
            results_pref = f"Showing {len(messages)} of {total} results (page {page}/{num_pages}):"
            results_formatted = [message.content[0].text for message in messages]
            results_str = f"{results_pref} {json_dumps(results_formatted)}"

        return results_str

    def archival_memory_search(
        self, agent_state: AgentState, actor: User, query: str, page: Optional[int] = 0, start: Optional[int] = 0
    ) -> Optional[str]:
        """
        Search archival memory using semantic (embedding-based) search.

        Args:
            query (str): String to search for.
            page (Optional[int]): Allows you to page through results. Only use on a follow-up query. Defaults to 0 (first page).
            start (Optional[int]): Starting index for the search results. Defaults to 0.

        Returns:
            str: Query result string
        """
        if page is None or (isinstance(page, str) and page.lower().strip() == "none"):
            page = 0
        try:
            page = int(page)
        except:
            raise ValueError(f"'page' argument must be an integer")

        count = RETRIEVAL_QUERY_DEFAULT_PAGE_SIZE

        try:
            # Get results using passage manager
            all_results = AgentManager().list_passages(
                actor=actor,
                agent_id=agent_state.id,
                query_text=query,
                limit=count + start,  # Request enough results to handle offset
                embedding_config=agent_state.embedding_config,
                embed_query=True,
            )

            # Apply pagination
            end = min(count + start, len(all_results))
            paged_results = all_results[start:end]

            # Format results to match previous implementation
            formatted_results = [{"timestamp": str(result.created_at), "content": result.text} for result in paged_results]

            return formatted_results, len(formatted_results)

        except Exception as e:
            raise e

    def archival_memory_insert(self, agent_state: AgentState, actor: User, content: str) -> Optional[str]:
        """
        Add to archival memory. Make sure to phrase the memory contents such that it can be easily queried later.

        Args:
            content (str): Content to write to the memory. All unicode (including emojis) are supported.

        Returns:
            Optional[str]: None is always returned as this function does not produce a response.
        """
        PassageManager().insert_passage(
            agent_state=agent_state,
            agent_id=agent_state.id,
            text=content,
            actor=actor,
        )
        AgentManager().rebuild_system_prompt(agent_id=agent_state.id, actor=actor, force=True)
        return None

    def core_memory_append(self, agent_state: "AgentState", actor: User, label: str, content: str) -> Optional[str]:
        """
        Append to the contents of core memory.

        Args:
            label (str): Section of the memory to be edited (persona or human).
            content (str): Content to write to the memory. All unicode (including emojis) are supported.

        Returns:
            Optional[str]: None is always returned as this function does not produce a response.
        """
        current_value = str(agent_state.memory.get_block(label).value)
        new_value = current_value + "\n" + str(content)
        agent_state.memory.update_block_value(label=label, value=new_value)
        AgentManager().update_memory_if_changed(agent_id=agent_state.id, new_memory=agent_state.memory, actor=actor)
        return None

    def core_memory_replace(
        self,
        agent_state: "AgentState",
        actor: User,
        label: str,
        old_content: str,
        new_content: str,
    ) -> Optional[str]:
        """
        Replace the contents of core memory. To delete memories, use an empty string for new_content.

        Args:
            label (str): Section of the memory to be edited (persona or human).
            old_content (str): String to replace. Must be an exact match.
            new_content (str): Content to write to the memory. All unicode (including emojis) are supported.

        Returns:
            Optional[str]: None is always returned as this function does not produce a response.
        """
        current_value = str(agent_state.memory.get_block(label).value)
        if old_content not in current_value:
            raise ValueError(f"Old content '{old_content}' not found in memory block '{label}'")
        new_value = current_value.replace(str(old_content), str(new_content))
        agent_state.memory.update_block_value(label=label, value=new_value)
        AgentManager().update_memory_if_changed(agent_id=agent_state.id, new_memory=agent_state.memory, actor=actor)
        return None

    def memory_replace(
        agent_state: "AgentState",
        actor: User,
        label: str,
        old_str: str,
        new_str: Optional[str] = None,
    ) -> str:
        """
        The memory_replace command allows you to replace a specific string in a memory
        block with a new string. This is used for making precise edits.

        Args:
            label (str): Section of the memory to be edited, identified by its label.
            old_str (str): The text to replace (must match exactly, including whitespace
                and indentation).
            new_str (Optional[str]): The new text to insert in place of the old text.
                Omit this argument to delete the old_str.

        Returns:
            str: The success message
        """
        import re

        if bool(re.search(r"\nLine \d+: ", old_str)):
            raise ValueError(
                "old_str contains a line number prefix, which is not allowed. "
                "Do not include line numbers when calling memory tools (line "
                "numbers are for display purposes only)."
            )
        if CORE_MEMORY_LINE_NUMBER_WARNING in old_str:
            raise ValueError(
                "old_str contains a line number warning, which is not allowed. "
                "Do not include line number information when calling memory tools "
                "(line numbers are for display purposes only)."
            )
        if bool(re.search(r"\nLine \d+: ", new_str)):
            raise ValueError(
                "new_str contains a line number prefix, which is not allowed. "
                "Do not include line numbers when calling memory tools (line "
                "numbers are for display purposes only)."
            )

        old_str = str(old_str).expandtabs()
        new_str = str(new_str).expandtabs()
        current_value = str(agent_state.memory.get_block(label).value).expandtabs()

        # Check if old_str is unique in the block
        occurences = current_value.count(old_str)
        if occurences == 0:
            raise ValueError(
                f"No replacement was performed, old_str `{old_str}` did not appear " f"verbatim in memory block with label `{label}`."
            )
        elif occurences > 1:
            content_value_lines = current_value.split("\n")
            lines = [idx + 1 for idx, line in enumerate(content_value_lines) if old_str in line]
            raise ValueError(
                f"No replacement was performed. Multiple occurrences of "
                f"old_str `{old_str}` in lines {lines}. Please ensure it is unique."
            )

        # Replace old_str with new_str
        new_value = current_value.replace(str(old_str), str(new_str))

        # Write the new content to the block
        agent_state.memory.update_block_value(label=label, value=new_value)

        AgentManager().update_memory_if_changed(agent_id=agent_state.id, new_memory=agent_state.memory, actor=actor)

        # Create a snippet of the edited section
        SNIPPET_LINES = 3
        replacement_line = current_value.split(old_str)[0].count("\n")
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n")
        snippet = "\n".join(new_value.split("\n")[start_line : end_line + 1])

        # Prepare the success message
        success_msg = f"The core memory block with label `{label}` has been edited. "
        # success_msg += self._make_output(
        #     snippet, f"a snippet of {path}", start_line + 1
        # )
        # success_msg += f"A snippet of core memory block `{label}`:\n{snippet}\n"
        success_msg += (
            "Review the changes and make sure they are as expected (correct indentation, "
            "no duplicate lines, etc). Edit the memory block again if necessary."
        )

        # return None
        return success_msg

    def memory_insert(
        agent_state: "AgentState",
        actor: User,
        label: str,
        new_str: str,
        insert_line: int = -1,
    ) -> str:
        """
        The memory_insert command allows you to insert text at a specific location
        in a memory block.

        Args:
            label (str): Section of the memory to be edited, identified by its label.
            new_str (str): The text to insert.
            insert_line (int): The line number after which to insert the text (0 for
                beginning of file). Defaults to -1 (end of the file).

        Returns:
            str: The success message
        """
        import re

        if bool(re.search(r"\nLine \d+: ", new_str)):
            raise ValueError(
                "new_str contains a line number prefix, which is not allowed. Do not "
                "include line numbers when calling memory tools (line numbers are for "
                "display purposes only)."
            )
        if CORE_MEMORY_LINE_NUMBER_WARNING in new_str:
            raise ValueError(
                "new_str contains a line number warning, which is not allowed. Do not "
                "include line number information when calling memory tools (line numbers "
                "are for display purposes only)."
            )

        current_value = str(agent_state.memory.get_block(label).value).expandtabs()
        new_str = str(new_str).expandtabs()
        current_value_lines = current_value.split("\n")
        n_lines = len(current_value_lines)

        # Check if we're in range, from 0 (pre-line), to 1 (first line), to n_lines (last line)
        if insert_line < 0 or insert_line > n_lines:
            raise ValueError(
                f"Invalid `insert_line` parameter: {insert_line}. It should be within "
                f"the range of lines of the memory block: {[0, n_lines]}, or -1 to "
                f"append to the end of the memory block."
            )

        # Insert the new string as a line
        SNIPPET_LINES = 3
        new_str_lines = new_str.split("\n")
        new_value_lines = current_value_lines[:insert_line] + new_str_lines + current_value_lines[insert_line:]
        snippet_lines = (
            current_value_lines[max(0, insert_line - SNIPPET_LINES) : insert_line]
            + new_str_lines
            + current_value_lines[insert_line : insert_line + SNIPPET_LINES]
        )

        # Collate into the new value to update
        new_value = "\n".join(new_value_lines)
        snippet = "\n".join(snippet_lines)

        # Write into the block
        agent_state.memory.update_block_value(label=label, value=new_value)

        AgentManager().update_memory_if_changed(agent_id=agent_state.id, new_memory=agent_state.memory, actor=actor)

        # Prepare the success message
        success_msg = f"The core memory block with label `{label}` has been edited. "
        # success_msg += self._make_output(
        #     snippet,
        #     "a snippet of the edited file",
        #     max(1, insert_line - SNIPPET_LINES + 1),
        # )
        # success_msg += f"A snippet of core memory block `{label}`:\n{snippet}\n"
        success_msg += (
            "Review the changes and make sure they are as expected (correct indentation, "
            "no duplicate lines, etc). Edit the memory block again if necessary."
        )

        return success_msg

    def memory_rethink(agent_state: "AgentState", actor: User, label: str, new_memory: str) -> str:
        """
        The memory_rethink command allows you to completely rewrite the contents of a
        memory block. Use this tool to make large sweeping changes (e.g. when you want
        to condense or reorganize the memory blocks), do NOT use this tool to make small
        precise edits (e.g. add or remove a line, replace a specific string, etc).

        Args:
            label (str): The memory block to be rewritten, identified by its label.
            new_memory (str): The new memory contents with information integrated from
                existing memory blocks and the conversation context.

        Returns:
            str: The success message
        """
        import re

        if bool(re.search(r"\nLine \d+: ", new_memory)):
            raise ValueError(
                "new_memory contains a line number prefix, which is not allowed. Do not "
                "include line numbers when calling memory tools (line numbers are for "
                "display purposes only)."
            )
        if CORE_MEMORY_LINE_NUMBER_WARNING in new_memory:
            raise ValueError(
                "new_memory contains a line number warning, which is not allowed. Do not "
                "include line number information when calling memory tools (line numbers "
                "are for display purposes only)."
            )

        if agent_state.memory.get_block(label) is None:
            agent_state.memory.create_block(label=label, value=new_memory)

        agent_state.memory.update_block_value(label=label, value=new_memory)

        AgentManager().update_memory_if_changed(agent_id=agent_state.id, new_memory=agent_state.memory, actor=actor)

        # Prepare the success message
        success_msg = f"The core memory block with label `{label}` has been edited. "
        # success_msg += self._make_output(
        #     snippet, f"a snippet of {path}", start_line + 1
        # )
        # success_msg += f"A snippet of core memory block `{label}`:\n{snippet}\n"
        success_msg += (
            "Review the changes and make sure they are as expected (correct indentation, "
            "no duplicate lines, etc). Edit the memory block again if necessary."
        )

        # return None
        return success_msg

    def memory_finish_edits(agent_state: "AgentState") -> None:
        """
        Call the memory_finish_edits command when you are finished making edits
        (integrating all new information) into the memory blocks. This function
        is called when the agent is done rethinking the memory.

        Returns:
            Optional[str]: None is always returned as this function does not produce a response.
        """
        return None


class LettaMultiAgentToolExecutor(ToolExecutor):
    """Executor for LETTA multi-agent core tools."""

    # TODO: Implement
    # def execute(self, function_name: str, function_args: dict, agent: "Agent", tool: Tool) -> ToolExecutionResult:
    #     callable_func = get_function_from_module(LETTA_MULTI_AGENT_TOOL_MODULE_NAME, function_name)
    #     function_args["self"] = agent  # need to attach self to arg since it's dynamically linked
    #     function_response = callable_func(**function_args)
    #     return ToolExecutionResult(func_return=function_response)


class ExternalComposioToolExecutor(ToolExecutor):
    """Executor for external Composio tools."""

    def execute(
        self,
        function_name: str,
        function_args: dict,
        agent_state: AgentState,
        tool: Tool,
        actor: User,
        sandbox_config: Optional[SandboxConfig] = None,
        sandbox_env_vars: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        action_name = generate_composio_action_from_func_name(tool.name)

        # Get entity ID from the agent_state
        entity_id = self._get_entity_id(agent_state)

        # Get composio_api_key
        composio_api_key = get_composio_api_key(actor=actor)

        # TODO (matt): Roll in execute_composio_action into this class
        function_response = execute_composio_action(
            action_name=action_name, args=function_args, api_key=composio_api_key, entity_id=entity_id
        )

        return ToolExecutionResult(
            status="success",
            func_return=function_response,
        )

    def _get_entity_id(self, agent_state: AgentState) -> Optional[str]:
        """Extract the entity ID from environment variables."""
        for env_var in agent_state.tool_exec_environment_variables:
            if env_var.key == COMPOSIO_ENTITY_ENV_VAR_KEY:
                return env_var.value
        return None


class ExternalMCPToolExecutor(ToolExecutor):
    """Executor for external MCP tools."""

    # TODO: Implement
    #
    # def execute(self, function_name: str, function_args: dict, agent_state: AgentState, tool: Tool, actor: User) -> ToolExecutionResult:
    #     # Get the server name from the tool tag
    #     server_name = self._extract_server_name(tool)
    #
    #     # Get the MCPClient
    #     mcp_client = self._get_mcp_client(agent, server_name)
    #
    #     # Validate tool exists
    #     self._validate_tool_exists(mcp_client, function_name, server_name)
    #
    #     # Execute the tool
    #     function_response, is_error = mcp_client.execute_tool(tool_name=function_name, tool_args=function_args)
    #
    #     return ToolExecutionResult(
    #         status="error" if is_error else "success",
    #         func_return=function_response,
    #     )
    #
    # def _extract_server_name(self, tool: Tool) -> str:
    #     """Extract server name from tool tags."""
    #     return tool.tags[0].split(":")[1]
    #
    # def _get_mcp_client(self, agent: "Agent", server_name: str):
    #     """Get the MCP client for the given server name."""
    #     if not agent.mcp_clients:
    #         raise ValueError("No MCP client available to use")
    #
    #     if server_name not in agent.mcp_clients:
    #         raise ValueError(f"Unknown MCP server name: {server_name}")
    #
    #     mcp_client = agent.mcp_clients[server_name]
    #     if not isinstance(mcp_client, BaseMCPClient):
    #         raise RuntimeError(f"Expected an MCPClient, but got: {type(mcp_client)}")
    #
    #     return mcp_client
    #
    # def _validate_tool_exists(self, mcp_client, function_name: str, server_name: str):
    #     """Validate that the tool exists in the MCP server."""
    #     available_tools = mcp_client.list_tools()
    #     available_tool_names = [t.name for t in available_tools]
    #
    #     if function_name not in available_tool_names:
    #         raise ValueError(
    #             f"{function_name} is not available in MCP server {server_name}. " f"Please check your `~/.letta/mcp_config.json` file."
    #         )


class SandboxToolExecutor(ToolExecutor):
    """Executor for sandboxed tools."""

    async def execute(
        self,
        function_name: str,
        function_args: dict,
        agent_state: AgentState,
        tool: Tool,
        actor: User,
        sandbox_config: Optional[SandboxConfig] = None,
        sandbox_env_vars: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:

        # Store original memory state
        orig_memory_str = agent_state.memory.compile()

        try:
            # Prepare function arguments
            function_args = self._prepare_function_args(function_args, tool, function_name)

            agent_state_copy = self._create_agent_state_copy(agent_state)

            # Execute in sandbox depending on API key
            if tool_settings.e2b_api_key:
                sandbox = AsyncToolSandboxE2B(
                    function_name, function_args, actor, tool_object=tool, sandbox_config=sandbox_config, sandbox_env_vars=sandbox_env_vars
                )
            else:
                sandbox = AsyncToolSandboxLocal(
                    function_name, function_args, actor, tool_object=tool, sandbox_config=sandbox_config, sandbox_env_vars=sandbox_env_vars
                )

            tool_execution_result = await sandbox.run(agent_state=agent_state_copy)

            # Verify memory integrity
            assert orig_memory_str == agent_state.memory.compile(), "Memory should not be modified in a sandbox tool"

            # Update agent memory if needed
            if tool_execution_result.agent_state is not None:
                AgentManager().update_memory_if_changed(agent_state.id, tool_execution_result.agent_state.memory, actor)

            return tool_execution_result

        except Exception as e:
            return self._handle_execution_error(e, function_name, traceback.format_exc())

    def _prepare_function_args(self, function_args: dict, tool: Tool, function_name: str) -> dict:
        """Prepare function arguments with proper type coercion."""
        try:
            # Parse the source code to extract function annotations
            annotations = get_function_annotations_from_source(tool.source_code, function_name)
            # Coerce the function arguments to the correct types based on the annotations
            return coerce_dict_args_by_annotations(function_args, annotations)
        except ValueError:
            # Just log the error and continue with original args
            # This is defensive programming - we try to coerce but fall back if it fails
            return function_args

    def _create_agent_state_copy(self, agent_state: AgentState):
        """Create a copy of agent state for sandbox execution."""
        agent_state_copy = agent_state.__deepcopy__()
        # Remove tools from copy to prevent nested tool execution
        agent_state_copy.tools = []
        agent_state_copy.tool_rules = []
        return agent_state_copy

    def _handle_execution_error(
        self,
        exception: Exception,
        function_name: str,
        stderr: str,
    ) -> ToolExecutionResult:
        """Handle tool execution errors."""
        error_message = get_friendly_error_msg(
            function_name=function_name, exception_name=type(exception).__name__, exception_message=str(exception)
        )
        return ToolExecutionResult(
            status="error",
            func_return=error_message,
            stderr=[stderr],
        )
