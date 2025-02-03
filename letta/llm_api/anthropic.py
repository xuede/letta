import json
import re
import time
import warnings
from typing import Generator, List, Optional, Union

import anthropic
from anthropic import PermissionDeniedError
from anthropic.types.beta import (
    BetaRawContentBlockDeltaEvent,
    BetaRawContentBlockStartEvent,
    BetaRawContentBlockStopEvent,
    BetaRawMessageDeltaEvent,
    BetaRawMessageStartEvent,
    BetaRawMessageStopEvent,
    BetaTextBlock,
    BetaToolUseBlock,
)

from letta.errors import BedrockError, BedrockPermissionError
from letta.llm_api.aws_bedrock import get_bedrock_client
from letta.local_llm.utils import num_tokens_from_functions, num_tokens_from_messages
from letta.schemas.message import Message as _Message
from letta.schemas.message import MessageRole as _MessageRole
from letta.schemas.openai.chat_completion_request import ChatCompletionRequest, Tool
from letta.schemas.openai.chat_completion_response import (
    ChatCompletionChunkResponse,
    ChatCompletionResponse,
    Choice,
    ChunkChoice,
    FunctionCall,
    FunctionCallDelta,
)
from letta.schemas.openai.chat_completion_response import Message
from letta.schemas.openai.chat_completion_response import Message as ChoiceMessage
from letta.schemas.openai.chat_completion_response import MessageDelta, ToolCall, ToolCallDelta, UsageStatistics
from letta.services.provider_manager import ProviderManager
from letta.settings import model_settings
from letta.streaming_interface import AgentChunkStreamingInterface, AgentRefreshStreamingInterface
from letta.utils import get_utc_time

BASE_URL = "https://api.anthropic.com/v1"


# https://docs.anthropic.com/claude/docs/models-overview
# Sadly hardcoded
MODEL_LIST = [
    {
        "name": "claude-3-opus-20240229",
        "context_window": 200000,
    },
    {
        "name": "claude-3-5-sonnet-20241022",
        "context_window": 200000,
    },
    {
        "name": "claude-3-5-haiku-20241022",
        "context_window": 200000,
    },
]

DUMMY_FIRST_USER_MESSAGE = "User initializing bootup sequence."


def antropic_get_model_context_window(url: str, api_key: Union[str, None], model: str) -> int:
    for model_dict in anthropic_get_model_list(url=url, api_key=api_key):
        if model_dict["name"] == model:
            return model_dict["context_window"]
    raise ValueError(f"Can't find model '{model}' in Anthropic model list")


def anthropic_get_model_list(url: str, api_key: Union[str, None]) -> dict:
    """https://docs.anthropic.com/claude/docs/models-overview"""

    # NOTE: currently there is no GET /models, so we need to hardcode
    return MODEL_LIST


def convert_tools_to_anthropic_format(tools: List[Tool]) -> List[dict]:
    """See: https://docs.anthropic.com/claude/docs/tool-use

    OpenAI style:
      "tools": [{
        "type": "function",
        "function": {
            "name": "find_movies",
            "description": "find ....",
            "parameters": {
              "type": "object",
              "properties": {
                 PARAM: {
                   "type": PARAM_TYPE,  # eg "string"
                   "description": PARAM_DESCRIPTION,
                 },
                 ...
              },
              "required": List[str],
            }
        }
      }
      ]

    Anthropic style:
      "tools": [{
        "name": "find_movies",
        "description": "find ....",
        "input_schema": {
          "type": "object",
          "properties": {
             PARAM: {
               "type": PARAM_TYPE,  # eg "string"
               "description": PARAM_DESCRIPTION,
             },
             ...
          },
          "required": List[str],
        }
      }
      ]

      Two small differences:
        - 1 level less of nesting
        - "parameters" -> "input_schema"
    """
    formatted_tools = []
    for tool in tools:
        formatted_tool = {
            "name": tool.function.name,
            "description": tool.function.description,
            "input_schema": tool.function.parameters or {"type": "object", "properties": {}, "required": []},
        }
        formatted_tools.append(formatted_tool)

    return formatted_tools


def merge_tool_results_into_user_messages(messages: List[dict]):
    """Anthropic API doesn't allow role 'tool'->'user' sequences

    Example HTTP error:
    messages: roles must alternate between "user" and "assistant", but found multiple "user" roles in a row

    From: https://docs.anthropic.com/claude/docs/tool-use
    You may be familiar with other APIs that return tool use as separate from the model's primary output,
    or which use a special-purpose tool or function message role.
    In contrast, Anthropic's models and API are built around alternating user and assistant messages,
    where each message is an array of rich content blocks: text, image, tool_use, and tool_result.
    """

    # TODO walk through the messages list
    # When a dict (dict_A) with 'role' == 'user' is followed by a dict with 'role' == 'user' (dict B), do the following
    # dict_A["content"] = dict_A["content"] + dict_B["content"]

    # The result should be a new merged_messages list that doesn't have any back-to-back dicts with 'role' == 'user'
    merged_messages = []
    if not messages:
        return merged_messages

    # Start with the first message in the list
    current_message = messages[0]

    for next_message in messages[1:]:
        if current_message["role"] == "user" and next_message["role"] == "user":
            # Merge contents of the next user message into current one
            current_content = (
                current_message["content"]
                if isinstance(current_message["content"], list)
                else [{"type": "text", "text": current_message["content"]}]
            )
            next_content = (
                next_message["content"]
                if isinstance(next_message["content"], list)
                else [{"type": "text", "text": next_message["content"]}]
            )
            merged_content = current_content + next_content
            current_message["content"] = merged_content
        else:
            # Append the current message to result as it's complete
            merged_messages.append(current_message)
            # Move on to the next message
            current_message = next_message

    # Append the last processed message to the result
    merged_messages.append(current_message)

    return merged_messages


def remap_finish_reason(stop_reason: str) -> str:
    """Remap Anthropic's 'stop_reason' to OpenAI 'finish_reason'

    OpenAI: 'stop', 'length', 'function_call', 'content_filter', null
    see: https://platform.openai.com/docs/guides/text-generation/chat-completions-api

    From: https://docs.anthropic.com/claude/reference/migrating-from-text-completions-to-messages#stop-reason

    Messages have a stop_reason of one of the following values:
        "end_turn": The conversational turn ended naturally.
        "stop_sequence": One of your specified custom stop sequences was generated.
        "max_tokens": (unchanged)

    """
    if stop_reason == "end_turn":
        return "stop"
    elif stop_reason == "stop_sequence":
        return "stop"
    elif stop_reason == "max_tokens":
        return "length"
    elif stop_reason == "tool_use":
        return "function_call"
    else:
        raise ValueError(f"Unexpected stop_reason: {stop_reason}")


def strip_xml_tags(string: str, tag: Optional[str]) -> str:
    if tag is None:
        return string
    # Construct the regular expression pattern to find the start and end tags
    tag_pattern = f"<{tag}.*?>|</{tag}>"
    # Use the regular expression to replace the tags with an empty string
    return re.sub(tag_pattern, "", string)


def strip_xml_tags_streaming(string: str, tag: Optional[str]) -> str:
    if tag is None:
        return string

    # Handle common partial tag cases
    parts_to_remove = [
        "<",  # Leftover start bracket
        f"<{tag}",  # Opening tag start
        f"</{tag}",  # Closing tag start
        f"/{tag}>",  # Closing tag end
        f"{tag}>",  # Opening tag end
        f"/{tag}",  # Partial closing tag without >
        ">",  # Leftover end bracket
    ]

    result = string
    for part in parts_to_remove:
        result = result.replace(part, "")

    return result


def convert_anthropic_response_to_chatcompletion(
    response: anthropic.types.Message,
    inner_thoughts_xml_tag: Optional[str] = None,
) -> ChatCompletionResponse:
    """
    Example response from Claude 3:
    response.json = {
        'id': 'msg_01W1xg9hdRzbeN2CfZM7zD2w',
        'type': 'message',
        'role': 'assistant',
        'content': [
            {
                'type': 'text',
                'text': "<thinking>Analyzing user login event. This is Chad's first
    interaction with me. I will adjust my personality and rapport accordingly.</thinking>"
            },
            {
                'type':
                'tool_use',
                'id': 'toolu_01Ka4AuCmfvxiidnBZuNfP1u',
                'name': 'core_memory_append',
                'input': {
                    'name': 'human',
                    'content': 'Chad is logging in for the first time. I will aim to build a warm
    and welcoming rapport.',
                    'request_heartbeat': True
                }
            }
        ],
        'model': 'claude-3-haiku-20240307',
        'stop_reason': 'tool_use',
        'stop_sequence': None,
        'usage': {
            'input_tokens': 3305,
            'output_tokens': 141
        }
    }
    """
    prompt_tokens = response.usage.input_tokens
    completion_tokens = response.usage.output_tokens
    finish_reason = remap_finish_reason(response.stop_reason)

    content = None
    tool_calls = None

    if len(response.content) > 1:
        # inner mono + function call
        assert len(response.content) == 2
        text_block = response.content[0]
        tool_block = response.content[1]
        assert text_block.type == "text"
        assert tool_block.type == "tool_use"
        content = strip_xml_tags(string=text_block.text, tag=inner_thoughts_xml_tag)
        tool_calls = [
            ToolCall(
                id=tool_block.id,
                type="function",
                function=FunctionCall(
                    name=tool_block.name,
                    arguments=json.dumps(tool_block.input, indent=2),
                ),
            )
        ]
    elif len(response.content) == 1:
        block = response.content[0]
        if block.type == "tool_use":
            # function call only
            tool_calls = [
                ToolCall(
                    id=block.id,
                    type="function",
                    function=FunctionCall(
                        name=block.name,
                        arguments=json.dumps(block.input, indent=2),
                    ),
                )
            ]
        else:
            # inner mono only
            content = strip_xml_tags(string=block.text, tag=inner_thoughts_xml_tag)
    else:
        raise RuntimeError("Unexpected empty content in response")

    assert response.role == "assistant"
    choice = Choice(
        index=0,
        finish_reason=finish_reason,
        message=ChoiceMessage(
            role=response.role,
            content=content,
            tool_calls=tool_calls,
        ),
    )

    return ChatCompletionResponse(
        id=response.id,
        choices=[choice],
        created=get_utc_time(),
        model=response.model,
        usage=UsageStatistics(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def convert_anthropic_stream_event_to_chatcompletion(
    event: Union[
        BetaRawMessageStartEvent,
        BetaRawContentBlockStartEvent,
        BetaRawContentBlockDeltaEvent,
        BetaRawContentBlockStopEvent,
        BetaRawMessageDeltaEvent,
        BetaRawMessageStopEvent,
    ],
    message_id: str,
    model: str,
    inner_thoughts_xml_tag: Optional[str] = "thinking",
) -> ChatCompletionChunkResponse:
    """Convert Anthropic stream events to OpenAI ChatCompletionResponse format.

        Args:
            event: The event to convert
            message_id: The ID of the message. Anthropic does not return this on every event, so we need to keep track of it
            model: The model used. Anthropic does not return this on every event, so we need to keep track of it

        Example response from OpenAI:

        'id': 'MESSAGE_ID',
        'choices': [
            {
                'finish_reason': None,
                'index': 0,
                'delta': {
                    'content': None,
                    'tool_calls': [
                        {
                            'index': 0,
                            'id': None,
                            'type': 'function',
                            'function': {
                                'name': None,
                                'arguments': '_th'
                            }
                        }
                    ],
                    'function_call': None
                },
                'logprobs': None
            }
        ],
        'created': datetime.datetime(2025, 1, 24, 0, 18, 55, tzinfo=TzInfo(UTC)),
        'model': 'gpt-4o-mini-2024-07-18',
        'system_fingerprint': 'fp_bd83329f63',
        'object': 'chat.completion.chunk'
    }
    """
    # Get finish reason
    finish_reason = None
    if isinstance(event, BetaRawMessageDeltaEvent):
        """
        BetaRawMessageDeltaEvent(
            delta=Delta(
                stop_reason='tool_use',
                stop_sequence=None
            ),
            type='message_delta',
            usage=BetaMessageDeltaUsage(output_tokens=45)
        )
        """
        finish_reason = remap_finish_reason(event.delta.stop_reason)

    # Get content and tool calls
    content = None
    tool_calls = None
    if isinstance(event, BetaRawContentBlockDeltaEvent):
        """
        BetaRawContentBlockDeltaEvent(
            delta=BetaInputJSONDelta(
                partial_json='lo',
                type='input_json_delta'
            ),
            index=0,
            type='content_block_delta'
        )

        OR

        BetaRawContentBlockDeltaEvent(
            delta=BetaTextDelta(
                text='👋 ',
                type='text_delta'
            ),
            index=0,
            type='content_block_delta'
        )

        """
        if event.delta.type == "text_delta":
            content = strip_xml_tags_streaming(string=event.delta.text, tag=inner_thoughts_xml_tag)

        elif event.delta.type == "input_json_delta":
            tool_calls = [
                ToolCallDelta(
                    index=0,
                    function=FunctionCallDelta(
                        name=None,
                        arguments=event.delta.partial_json,
                    ),
                )
            ]
    elif isinstance(event, BetaRawContentBlockStartEvent):
        """
        BetaRawContentBlockStartEvent(
             content_block=BetaToolUseBlock(
                 id='toolu_01LmpZhRhR3WdrRdUrfkKfFw',
                 input={},
                 name='get_weather',
                 type='tool_use'
             ),
             index=0,
             type='content_block_start'
         )

         OR

         BetaRawContentBlockStartEvent(
             content_block=BetaTextBlock(
                 text='',
                 type='text'
             ),
             index=0,
             type='content_block_start'
         )
        """
        if isinstance(event.content_block, BetaToolUseBlock):
            tool_calls = [
                ToolCallDelta(
                    index=0,
                    id=event.content_block.id,
                    function=FunctionCallDelta(
                        name=event.content_block.name,
                        arguments="",
                    ),
                )
            ]
        elif isinstance(event.content_block, BetaTextBlock):
            content = event.content_block.text

    # Initialize base response
    choice = ChunkChoice(
        index=0,
        finish_reason=finish_reason,
        delta=MessageDelta(
            content=content,
            tool_calls=tool_calls,
        ),
    )
    return ChatCompletionChunkResponse(
        id=message_id,
        choices=[choice],
        created=get_utc_time(),
        model=model,
    )


def _prepare_anthropic_request(
    data: ChatCompletionRequest,
    inner_thoughts_xml_tag: Optional[str] = "thinking",
) -> dict:
    """Prepare the request data for Anthropic API format."""
    # convert the tools
    anthropic_tools = None if data.tools is None else convert_tools_to_anthropic_format(data.tools)

    # pydantic -> dict
    data = data.model_dump(exclude_none=True)

    if "functions" in data:
        raise ValueError(f"'functions' unexpected in Anthropic API payload")

    # Handle tools
    if "tools" in data and data["tools"] is None:
        data.pop("tools")
        data.pop("tool_choice", None)
    elif anthropic_tools is not None:
        data["tools"] = anthropic_tools
        if len(anthropic_tools) == 1:
            data["tool_choice"] = {
                "type": "tool",
                "name": anthropic_tools[0]["name"],
                "disable_parallel_tool_use": True,
            }

    # Move 'system' to the top level
    assert data["messages"][0]["role"] == "system", f"Expected 'system' role in messages[0]:\n{data['messages'][0]}"
    data["system"] = data["messages"][0]["content"]
    data["messages"] = data["messages"][1:]

    # Process messages
    for message in data["messages"]:
        if "content" not in message:
            message["content"] = None

    # Convert to Anthropic format
    msg_objs = [_Message.dict_to_message(user_id=None, agent_id=None, openai_message_dict=m) for m in data["messages"]]
    data["messages"] = [m.to_anthropic_dict(inner_thoughts_xml_tag=inner_thoughts_xml_tag) for m in msg_objs]

    # Ensure first message is user
    if data["messages"][0]["role"] != "user":
        data["messages"] = [{"role": "user", "content": DUMMY_FIRST_USER_MESSAGE}] + data["messages"]

    # Handle alternating messages
    data["messages"] = merge_tool_results_into_user_messages(data["messages"])

    # Validate max_tokens
    assert "max_tokens" in data, data

    # Remove OpenAI-specific fields
    for field in ["frequency_penalty", "logprobs", "n", "top_p", "presence_penalty", "user", "stream"]:
        data.pop(field, None)

    return data


def anthropic_chat_completions_request(
    data: ChatCompletionRequest,
    inner_thoughts_xml_tag: Optional[str] = "thinking",
    betas: List[str] = ["tools-2024-04-04"],
) -> ChatCompletionResponse:
    """https://docs.anthropic.com/claude/docs/tool-use"""
    anthropic_client = None
    anthropic_override_key = ProviderManager().get_anthropic_override_key()
    if anthropic_override_key:
        anthropic_client = anthropic.Anthropic(api_key=anthropic_override_key)
    elif model_settings.anthropic_api_key:
        anthropic_client = anthropic.Anthropic()
    data = _prepare_anthropic_request(data, inner_thoughts_xml_tag)
    response = anthropic_client.beta.messages.create(
        **data,
        betas=betas,
    )
    return convert_anthropic_response_to_chatcompletion(response=response, inner_thoughts_xml_tag=inner_thoughts_xml_tag)


def anthropic_bedrock_chat_completions_request(
    data: ChatCompletionRequest,
    inner_thoughts_xml_tag: Optional[str] = "thinking",
) -> ChatCompletionResponse:
    """Make a chat completion request to Anthropic via AWS Bedrock."""
    data = _prepare_anthropic_request(data, inner_thoughts_xml_tag)

    # Get the client
    client = get_bedrock_client()

    # Make the request
    try:
        response = client.messages.create(**data)
        return convert_anthropic_response_to_chatcompletion(response=response, inner_thoughts_xml_tag=inner_thoughts_xml_tag)
    except PermissionDeniedError:
        raise BedrockPermissionError(f"User does not have access to the Bedrock model with the specified ID. {data['model']}")
    except Exception as e:
        raise BedrockError(f"Bedrock error: {e}")


def anthropic_chat_completions_request_stream(
    data: ChatCompletionRequest,
    inner_thoughts_xml_tag: Optional[str] = "thinking",
    betas: List[str] = ["tools-2024-04-04"],
) -> Generator[ChatCompletionChunkResponse, None, None]:
    """Stream chat completions from Anthropic API.

    Similar to OpenAI's streaming, but using Anthropic's native streaming support.
    See: https://docs.anthropic.com/claude/reference/messages-streaming
    """
    data = _prepare_anthropic_request(data, inner_thoughts_xml_tag)

    anthropic_override_key = ProviderManager().get_anthropic_override_key()
    if anthropic_override_key:
        anthropic_client = anthropic.Anthropic(api_key=anthropic_override_key)
    elif model_settings.anthropic_api_key:
        anthropic_client = anthropic.Anthropic()

    with anthropic_client.beta.messages.stream(
        **data,
        betas=betas,
    ) as stream:
        # Stream: https://github.com/anthropics/anthropic-sdk-python/blob/d212ec9f6d5e956f13bc0ddc3d86b5888a954383/src/anthropic/lib/streaming/_beta_messages.py#L22
        message_id = None
        model = None

        for chunk in stream._raw_stream:
            time.sleep(0.01)  # Anthropic is really fast, faster than frontend can upload.
            if isinstance(chunk, BetaRawMessageStartEvent):
                """
                BetaRawMessageStartEvent(
                    message=BetaMessage(
                        id='MESSAGE ID HERE',
                        content=[],
                        model='claude-3-5-sonnet-20241022',
                        role='assistant',
                        stop_reason=None,
                        stop_sequence=None,
                        type='message',
                        usage=BetaUsage(
                            cache_creation_input_tokens=0,
                            cache_read_input_tokens=0,
                            input_tokens=30,
                            output_tokens=4
                        )
                    ),
                    type='message_start'
                ),
                """
                message_id = chunk.message.id
                model = chunk.message.model
            yield convert_anthropic_stream_event_to_chatcompletion(chunk, message_id, model, inner_thoughts_xml_tag)


def anthropic_chat_completions_process_stream(
    chat_completion_request: ChatCompletionRequest,
    stream_interface: Optional[Union[AgentChunkStreamingInterface, AgentRefreshStreamingInterface]] = None,
    inner_thoughts_xml_tag: Optional[str] = "thinking",
    create_message_id: bool = True,
    create_message_datetime: bool = True,
    betas: List[str] = ["tools-2024-04-04"],
) -> ChatCompletionResponse:
    """Process a streaming completion response from Anthropic, similar to OpenAI's streaming.

    Args:
        api_key: The Anthropic API key
        chat_completion_request: The chat completion request
        stream_interface: Interface for handling streaming chunks
        inner_thoughts_xml_tag: Tag for inner thoughts in the response
        create_message_id: Whether to create a message ID
        create_message_datetime: Whether to create message datetime
        betas: Beta features to enable

    Returns:
        The final ChatCompletionResponse
    """
    assert chat_completion_request.stream == True
    assert stream_interface is not None, "Required"

    # Count prompt tokens - we'll get completion tokens from the final response
    chat_history = [m.model_dump(exclude_none=True) for m in chat_completion_request.messages]
    prompt_tokens = num_tokens_from_messages(
        messages=chat_history,
        model=chat_completion_request.model,
    )

    # Add tokens for tools if present
    if chat_completion_request.tools is not None:
        assert chat_completion_request.functions is None
        prompt_tokens += num_tokens_from_functions(
            functions=[t.function.model_dump() for t in chat_completion_request.tools],
            model=chat_completion_request.model,
        )
    elif chat_completion_request.functions is not None:
        assert chat_completion_request.tools is None
        prompt_tokens += num_tokens_from_functions(
            functions=[f.model_dump() for f in chat_completion_request.functions],
            model=chat_completion_request.model,
        )

    # Create a dummy message for ID/datetime if needed
    dummy_message = _Message(
        role=_MessageRole.assistant,
        text="",
        agent_id="",
        model="",
        name=None,
        tool_calls=None,
        tool_call_id=None,
    )

    TEMP_STREAM_RESPONSE_ID = "temp_id"
    TEMP_STREAM_FINISH_REASON = "temp_null"
    TEMP_STREAM_TOOL_CALL_ID = "temp_id"
    chat_completion_response = ChatCompletionResponse(
        id=dummy_message.id if create_message_id else TEMP_STREAM_RESPONSE_ID,
        choices=[],
        created=dummy_message.created_at,
        model=chat_completion_request.model,
        usage=UsageStatistics(
            completion_tokens=0,
            prompt_tokens=prompt_tokens,
            total_tokens=prompt_tokens,
        ),
    )

    if stream_interface:
        stream_interface.stream_start()

    n_chunks = 0
    try:
        for chunk_idx, chat_completion_chunk in enumerate(
            anthropic_chat_completions_request_stream(
                data=chat_completion_request,
                inner_thoughts_xml_tag=inner_thoughts_xml_tag,
                betas=betas,
            )
        ):
            assert isinstance(chat_completion_chunk, ChatCompletionChunkResponse), type(chat_completion_chunk)

            if stream_interface:
                if isinstance(stream_interface, AgentChunkStreamingInterface):
                    stream_interface.process_chunk(
                        chat_completion_chunk,
                        message_id=chat_completion_response.id if create_message_id else chat_completion_chunk.id,
                        message_date=chat_completion_response.created if create_message_datetime else chat_completion_chunk.created,
                    )
                elif isinstance(stream_interface, AgentRefreshStreamingInterface):
                    stream_interface.process_refresh(chat_completion_response)
                else:
                    raise TypeError(stream_interface)

            if chunk_idx == 0:
                # initialize the choice objects which we will increment with the deltas
                num_choices = len(chat_completion_chunk.choices)
                assert num_choices > 0
                chat_completion_response.choices = [
                    Choice(
                        finish_reason=TEMP_STREAM_FINISH_REASON,  # NOTE: needs to be ovrerwritten
                        index=i,
                        message=Message(
                            role="assistant",
                        ),
                    )
                    for i in range(len(chat_completion_chunk.choices))
                ]

            # add the choice delta
            assert len(chat_completion_chunk.choices) == len(chat_completion_response.choices), chat_completion_chunk
            for chunk_choice in chat_completion_chunk.choices:
                if chunk_choice.finish_reason is not None:
                    chat_completion_response.choices[chunk_choice.index].finish_reason = chunk_choice.finish_reason

                if chunk_choice.logprobs is not None:
                    chat_completion_response.choices[chunk_choice.index].logprobs = chunk_choice.logprobs

                accum_message = chat_completion_response.choices[chunk_choice.index].message
                message_delta = chunk_choice.delta

                if message_delta.content is not None:
                    content_delta = message_delta.content
                    if accum_message.content is None:
                        accum_message.content = content_delta
                    else:
                        accum_message.content += content_delta

                # TODO(charles) make sure this works for parallel tool calling?
                if message_delta.tool_calls is not None:
                    tool_calls_delta = message_delta.tool_calls

                    # If this is the first tool call showing up in a chunk, initialize the list with it
                    if accum_message.tool_calls is None:
                        accum_message.tool_calls = [
                            ToolCall(id=TEMP_STREAM_TOOL_CALL_ID, function=FunctionCall(name="", arguments=""))
                            for _ in range(len(tool_calls_delta))
                        ]

                    # There may be many tool calls in a tool calls delta (e.g. parallel tool calls)
                    for tool_call_delta in tool_calls_delta:
                        if tool_call_delta.id is not None:
                            # TODO assert that we're not overwriting?
                            # TODO += instead of =?
                            if tool_call_delta.index not in range(len(accum_message.tool_calls)):
                                warnings.warn(
                                    f"Tool call index out of range ({tool_call_delta.index})\ncurrent tool calls: {accum_message.tool_calls}\ncurrent delta: {tool_call_delta}"
                                )
                                # force index 0
                                # accum_message.tool_calls[0].id = tool_call_delta.id
                            else:
                                accum_message.tool_calls[tool_call_delta.index].id = tool_call_delta.id
                        if tool_call_delta.function is not None:
                            if tool_call_delta.function.name is not None:
                                # TODO assert that we're not overwriting?
                                # TODO += instead of =?
                                if tool_call_delta.index not in range(len(accum_message.tool_calls)):
                                    warnings.warn(
                                        f"Tool call index out of range ({tool_call_delta.index})\ncurrent tool calls: {accum_message.tool_calls}\ncurrent delta: {tool_call_delta}"
                                    )
                                    # force index 0
                                    # accum_message.tool_calls[0].function.name = tool_call_delta.function.name
                                else:
                                    accum_message.tool_calls[tool_call_delta.index].function.name = tool_call_delta.function.name
                            if tool_call_delta.function.arguments is not None:
                                if tool_call_delta.index not in range(len(accum_message.tool_calls)):
                                    warnings.warn(
                                        f"Tool call index out of range ({tool_call_delta.index})\ncurrent tool calls: {accum_message.tool_calls}\ncurrent delta: {tool_call_delta}"
                                    )
                                    # force index 0
                                    # accum_message.tool_calls[0].function.arguments += tool_call_delta.function.arguments
                                else:
                                    accum_message.tool_calls[tool_call_delta.index].function.arguments += tool_call_delta.function.arguments

                if message_delta.function_call is not None:
                    raise NotImplementedError(f"Old function_call style not support with stream=True")

            # overwrite response fields based on latest chunk
            if not create_message_id:
                chat_completion_response.id = chat_completion_chunk.id
            if not create_message_datetime:
                chat_completion_response.created = chat_completion_chunk.created
            chat_completion_response.model = chat_completion_chunk.model
            chat_completion_response.system_fingerprint = chat_completion_chunk.system_fingerprint

            # increment chunk counter
            n_chunks += 1

    except Exception as e:
        if stream_interface:
            stream_interface.stream_end()
        print(f"Parsing ChatCompletion stream failed with error:\n{str(e)}")
        raise e
    finally:
        if stream_interface:
            stream_interface.stream_end()

    # make sure we didn't leave temp stuff in
    assert all([c.finish_reason != TEMP_STREAM_FINISH_REASON for c in chat_completion_response.choices])
    assert all(
        [
            all([tc.id != TEMP_STREAM_TOOL_CALL_ID for tc in c.message.tool_calls]) if c.message.tool_calls else True
            for c in chat_completion_response.choices
        ]
    )
    if not create_message_id:
        assert chat_completion_response.id != dummy_message.id

    # compute token usage before returning
    # TODO try actually computing the #tokens instead of assuming the chunks is the same
    chat_completion_response.usage.completion_tokens = n_chunks
    chat_completion_response.usage.total_tokens = prompt_tokens + n_chunks

    assert len(chat_completion_response.choices) > 0, chat_completion_response

    return chat_completion_response
