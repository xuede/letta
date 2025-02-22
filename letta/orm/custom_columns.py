from sqlalchemy import JSON
from sqlalchemy.types import BINARY, TypeDecorator

from letta.helpers.converters import (
    deserialize_embedding_config,
    deserialize_llm_config,
    deserialize_tool_calls,
    deserialize_tool_rules,
    deserialize_vector,
    serialize_embedding_config,
    serialize_llm_config,
    serialize_tool_calls,
    serialize_tool_rules,
    serialize_vector,
)


class LLMConfigColumn(TypeDecorator):
    """Custom SQLAlchemy column type for storing LLMConfig as JSON."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return serialize_llm_config(value)

    def process_result_value(self, value, dialect):
        return deserialize_llm_config(value)


class EmbeddingConfigColumn(TypeDecorator):
    """Custom SQLAlchemy column type for storing EmbeddingConfig as JSON."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return serialize_embedding_config(value)

    def process_result_value(self, value, dialect):
        return deserialize_embedding_config(value)


class ToolRulesColumn(TypeDecorator):
    """Custom SQLAlchemy column type for storing a list of ToolRules as JSON."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return serialize_tool_rules(value)

    def process_result_value(self, value, dialect):
        return deserialize_tool_rules(value)


class ToolCallColumn(TypeDecorator):
    """Custom SQLAlchemy column type for storing OpenAI ToolCall objects as JSON."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return serialize_tool_calls(value)

    def process_result_value(self, value, dialect):
        return deserialize_tool_calls(value)


class CommonVector(TypeDecorator):
    """Custom SQLAlchemy column type for storing vectors in SQLite."""

    impl = BINARY
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return serialize_vector(value)

    def process_result_value(self, value, dialect):
        return deserialize_vector(value, dialect)
