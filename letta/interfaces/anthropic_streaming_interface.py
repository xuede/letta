from datetime import datetime, timezone
from enum import Enum
from typing import AsyncGenerator, List, Union

from anthropic import AsyncStream
from anthropic.types.beta import (
    BetaInputJSONDelta,
    BetaRawContentBlockDeltaEvent,
    BetaRawContentBlockStartEvent,
    BetaRawContentBlockStopEvent,
    BetaRawMessageDeltaEvent,
    BetaRawMessageStartEvent,
    BetaRawMessageStopEvent,
    BetaRawMessageStreamEvent,
    BetaRedactedThinkingBlock,
    BetaSignatureDelta,
    BetaTextBlock,
    BetaTextDelta,
    BetaThinkingBlock,
    BetaThinkingDelta,
    BetaToolUseBlock,
)

from letta.constants import DEFAULT_MESSAGE_TOOL, DEFAULT_MESSAGE_TOOL_KWARG
from letta.local_llm.constants import INNER_THOUGHTS_KWARG
from letta.log import get_logger
from letta.schemas.letta_message import (
    AssistantMessage,
    HiddenReasoningMessage,
    LettaMessage,
    ReasoningMessage,
    ToolCallDelta,
    ToolCallMessage,
)
from letta.schemas.letta_message_content import ReasoningContent, RedactedReasoningContent, TextContent
from letta.schemas.message import Message
from letta.schemas.openai.chat_completion_response import FunctionCall, ToolCall
from letta.server.rest_api.optimistic_json_parser import OptimisticJSONParser

logger = get_logger(__name__)


# TODO: These modes aren't used right now - but can be useful we do multiple sequential tool calling within one Claude message
class EventMode(Enum):
    TEXT = "TEXT"
    TOOL_USE = "TOOL_USE"
    THINKING = "THINKING"
    REDACTED_THINKING = "REDACTED_THINKING"


class AnthropicStreamingInterface:
    """
    Encapsulates the logic for streaming responses from Anthropic.
    This class handles parsing of partial tokens, pre-execution messages,
    and detection of tool call events.
    """

    def __init__(self, use_assistant_message: bool = False, put_inner_thoughts_in_kwarg: bool = False):
        self.optimistic_json_parser: OptimisticJSONParser = OptimisticJSONParser()
        self.use_assistant_message = use_assistant_message

        # Premake IDs for database writes
        self.letta_assistant_message_id = Message.generate_id()
        self.letta_tool_message_id = Message.generate_id()

        self.anthropic_mode = None
        self.message_id = None
        self.accumulated_inner_thoughts = []
        self.tool_call_id = None
        self.tool_call_name = None
        self.accumulated_tool_call_args = []
        self.previous_parse = {}

        # usage trackers
        self.input_tokens = 0
        self.output_tokens = 0

        # reasoning object trackers
        self.reasoning_messages = []

        # Buffer to hold tool call messages until inner thoughts are complete
        self.tool_call_buffer = []
        self.inner_thoughts_complete = False
        self.put_inner_thoughts_in_kwarg = put_inner_thoughts_in_kwarg

    def get_tool_call_object(self) -> ToolCall:
        """Useful for agent loop"""
        return ToolCall(
            id=self.tool_call_id, function=FunctionCall(arguments="".join(self.accumulated_tool_call_args), name=self.tool_call_name)
        )

    def _check_inner_thoughts_complete(self, combined_args: str) -> bool:
        """
        Check if inner thoughts are complete in the current tool call arguments
        by looking for a closing quote after the inner_thoughts field
        """
        if not self.put_inner_thoughts_in_kwarg:
            # None of the things should have inner thoughts in kwargs
            return True
        else:
            parsed = self.optimistic_json_parser.parse(combined_args)
            # TODO: This will break on tools with 0 input
            return len(parsed.keys()) > 1 and INNER_THOUGHTS_KWARG in parsed.keys()

    async def process(self, stream: AsyncStream[BetaRawMessageStreamEvent]) -> AsyncGenerator[LettaMessage, None]:
        async with stream:
            async for event in stream:
                # TODO: Support BetaThinkingBlock, BetaRedactedThinkingBlock
                if isinstance(event, BetaRawContentBlockStartEvent):
                    content = event.content_block

                    if isinstance(content, BetaTextBlock):
                        self.anthropic_mode = EventMode.TEXT
                        # TODO: Can capture citations, etc.
                    elif isinstance(content, BetaToolUseBlock):
                        self.anthropic_mode = EventMode.TOOL_USE
                        self.tool_call_id = content.id
                        self.tool_call_name = content.name
                        self.inner_thoughts_complete = False

                        if not self.use_assistant_message:
                            # Buffer the initial tool call message instead of yielding immediately
                            tool_call_msg = ToolCallMessage(
                                id=self.letta_tool_message_id,
                                tool_call=ToolCallDelta(name=self.tool_call_name, tool_call_id=self.tool_call_id),
                                date=datetime.now(timezone.utc).isoformat(),
                            )
                            self.tool_call_buffer.append(tool_call_msg)
                    elif isinstance(content, BetaThinkingBlock):
                        self.anthropic_mode = EventMode.THINKING
                        # TODO: Can capture signature, etc.
                    elif isinstance(content, BetaRedactedThinkingBlock):
                        self.anthropic_mode = EventMode.REDACTED_THINKING

                        hidden_reasoning_message = HiddenReasoningMessage(
                            id=self.letta_assistant_message_id,
                            state="redacted",
                            hidden_reasoning=content.data,
                            date=datetime.now(timezone.utc).isoformat(),
                        )
                        self.reasoning_messages.append(hidden_reasoning_message)
                        yield hidden_reasoning_message

                elif isinstance(event, BetaRawContentBlockDeltaEvent):
                    delta = event.delta

                    if isinstance(delta, BetaTextDelta):
                        # Safety check
                        if not self.anthropic_mode == EventMode.TEXT:
                            raise RuntimeError(
                                f"Streaming integrity failed - received BetaTextDelta object while not in TEXT EventMode: {delta}"
                            )

                        # TODO: Strip out </thinking> more robustly, this is pretty hacky lol
                        delta.text = delta.text.replace("</thinking>", "")
                        self.accumulated_inner_thoughts.append(delta.text)

                        reasoning_message = ReasoningMessage(
                            id=self.letta_assistant_message_id,
                            reasoning=self.accumulated_inner_thoughts[-1],
                            date=datetime.now(timezone.utc).isoformat(),
                        )
                        self.reasoning_messages.append(reasoning_message)
                        yield reasoning_message

                    elif isinstance(delta, BetaInputJSONDelta):
                        if not self.anthropic_mode == EventMode.TOOL_USE:
                            raise RuntimeError(
                                f"Streaming integrity failed - received BetaInputJSONDelta object while not in TOOL_USE EventMode: {delta}"
                            )

                        self.accumulated_tool_call_args.append(delta.partial_json)
                        combined_args = "".join(self.accumulated_tool_call_args)
                        current_parsed = self.optimistic_json_parser.parse(combined_args)

                        # Start detecting a difference in inner thoughts
                        previous_inner_thoughts = self.previous_parse.get(INNER_THOUGHTS_KWARG, "")
                        current_inner_thoughts = current_parsed.get(INNER_THOUGHTS_KWARG, "")
                        inner_thoughts_diff = current_inner_thoughts[len(previous_inner_thoughts) :]

                        if inner_thoughts_diff:
                            reasoning_message = ReasoningMessage(
                                id=self.letta_assistant_message_id,
                                reasoning=inner_thoughts_diff,
                                date=datetime.now(timezone.utc).isoformat(),
                            )
                            self.reasoning_messages.append(reasoning_message)
                            yield reasoning_message

                        # Check if inner thoughts are complete - if so, flush the buffer
                        if not self.inner_thoughts_complete and self._check_inner_thoughts_complete(combined_args):
                            self.inner_thoughts_complete = True
                            # Flush all buffered tool call messages
                            for buffered_msg in self.tool_call_buffer:
                                yield buffered_msg
                            self.tool_call_buffer = []

                        # Start detecting special case of "send_message"
                        if self.tool_call_name == DEFAULT_MESSAGE_TOOL and self.use_assistant_message:
                            previous_send_message = self.previous_parse.get(DEFAULT_MESSAGE_TOOL_KWARG, "")
                            current_send_message = current_parsed.get(DEFAULT_MESSAGE_TOOL_KWARG, "")
                            send_message_diff = current_send_message[len(previous_send_message) :]

                            # Only stream out if it's not an empty string
                            if send_message_diff:
                                yield AssistantMessage(
                                    id=self.letta_assistant_message_id,
                                    content=[TextContent(text=send_message_diff)],
                                    date=datetime.now(timezone.utc).isoformat(),
                                )
                        else:
                            # Otherwise, it is a normal tool call - buffer or yield based on inner thoughts status
                            tool_call_msg = ToolCallMessage(
                                id=self.letta_tool_message_id,
                                tool_call=ToolCallDelta(arguments=delta.partial_json),
                                date=datetime.now(timezone.utc).isoformat(),
                            )

                            if self.inner_thoughts_complete:
                                yield tool_call_msg
                            else:
                                self.tool_call_buffer.append(tool_call_msg)

                        # Set previous parse
                        self.previous_parse = current_parsed
                    elif isinstance(delta, BetaThinkingDelta):
                        # Safety check
                        if not self.anthropic_mode == EventMode.THINKING:
                            raise RuntimeError(
                                f"Streaming integrity failed - received BetaThinkingBlock object while not in THINKING EventMode: {delta}"
                            )

                        reasoning_message = ReasoningMessage(
                            id=self.letta_assistant_message_id,
                            source="reasoner_model",
                            reasoning=delta.thinking,
                            date=datetime.now(timezone.utc).isoformat(),
                        )
                        self.reasoning_messages.append(reasoning_message)
                        yield reasoning_message
                    elif isinstance(delta, BetaSignatureDelta):
                        # Safety check
                        if not self.anthropic_mode == EventMode.THINKING:
                            raise RuntimeError(
                                f"Streaming integrity failed - received BetaSignatureDelta object while not in THINKING EventMode: {delta}"
                            )

                        reasoning_message = ReasoningMessage(
                            id=self.letta_assistant_message_id,
                            source="reasoner_model",
                            reasoning="",
                            date=datetime.now(timezone.utc).isoformat(),
                            signature=delta.signature,
                        )
                        self.reasoning_messages.append(reasoning_message)
                        yield reasoning_message
                elif isinstance(event, BetaRawMessageStartEvent):
                    self.message_id = event.message.id
                    self.input_tokens += event.message.usage.input_tokens
                    self.output_tokens += event.message.usage.output_tokens
                elif isinstance(event, BetaRawMessageDeltaEvent):
                    self.output_tokens += event.usage.output_tokens
                elif isinstance(event, BetaRawMessageStopEvent):
                    # Don't do anything here! We don't want to stop the stream.
                    pass
                elif isinstance(event, BetaRawContentBlockStopEvent):
                    # If we're exiting a tool use block and there are still buffered messages,
                    # we should flush them now
                    if self.anthropic_mode == EventMode.TOOL_USE and self.tool_call_buffer:
                        for buffered_msg in self.tool_call_buffer:
                            yield buffered_msg
                        self.tool_call_buffer = []

                    self.anthropic_mode = None

    def get_reasoning_content(self) -> List[Union[TextContent, ReasoningContent, RedactedReasoningContent]]:
        def _process_group(
            group: List[Union[ReasoningMessage, HiddenReasoningMessage]], group_type: str
        ) -> Union[TextContent, ReasoningContent, RedactedReasoningContent]:
            if group_type == "reasoning":
                reasoning_text = "".join(chunk.reasoning for chunk in group)
                is_native = any(chunk.source == "reasoner_model" for chunk in group)
                signature = next((chunk.signature for chunk in group if chunk.signature is not None), None)
                if is_native:
                    return ReasoningContent(is_native=is_native, reasoning=reasoning_text, signature=signature)
                else:
                    return TextContent(text=reasoning_text)
            elif group_type == "redacted":
                redacted_text = "".join(chunk.hidden_reasoning for chunk in group if chunk.hidden_reasoning is not None)
                return RedactedReasoningContent(data=redacted_text)
            else:
                raise ValueError("Unexpected group type")

        merged = []
        current_group = []
        current_group_type = None  # "reasoning" or "redacted"

        for msg in self.reasoning_messages:
            # Determine the type of the current message
            if isinstance(msg, HiddenReasoningMessage):
                msg_type = "redacted"
            elif isinstance(msg, ReasoningMessage):
                msg_type = "reasoning"
            else:
                raise ValueError("Unexpected message type")

            # Initialize group type if not set
            if current_group_type is None:
                current_group_type = msg_type

            # If the type changes, process the current group
            if msg_type != current_group_type:
                merged.append(_process_group(current_group, current_group_type))
                current_group = []
                current_group_type = msg_type

            current_group.append(msg)

        # Process the final group, if any.
        if current_group:
            merged.append(_process_group(current_group, current_group_type))

        # Strip out XML from any text content fields
        for content in merged:
            if isinstance(content, TextContent) and content.text.endswith("</thinking>"):
                cutoff = len(content.text) - len("</thinking>")
                content.text = content.text[:cutoff]

        return merged
