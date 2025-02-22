from typing import List, Optional

from sqlalchemy import and_, or_

from letta.orm.agent import Agent as AgentModel
from letta.orm.errors import NoResultFound
from letta.orm.message import Message as MessageModel
from letta.schemas.enums import MessageRole
from letta.schemas.message import Message as PydanticMessage
from letta.schemas.message import MessageUpdate
from letta.schemas.user import User as PydanticUser
from letta.utils import enforce_types


class MessageManager:
    """Manager class to handle business logic related to Messages."""

    def __init__(self):
        from letta.server.db import db_context

        self.session_maker = db_context

    @enforce_types
    def get_message_by_id(self, message_id: str, actor: PydanticUser) -> Optional[PydanticMessage]:
        """Fetch a message by ID."""
        with self.session_maker() as session:
            try:
                message = MessageModel.read(db_session=session, identifier=message_id, actor=actor)
                return message.to_pydantic()
            except NoResultFound:
                return None

    @enforce_types
    def get_messages_by_ids(self, message_ids: List[str], actor: PydanticUser) -> List[PydanticMessage]:
        """Fetch messages by ID and return them in the requested order."""
        with self.session_maker() as session:
            results = MessageModel.list(db_session=session, id=message_ids, organization_id=actor.organization_id, limit=len(message_ids))

            if len(results) != len(message_ids):
                raise NoResultFound(
                    f"Expected {len(message_ids)} messages, but found {len(results)}. Missing ids={set(message_ids) - set([r.id for r in results])}"
                )

            # Sort results directly based on message_ids
            result_dict = {msg.id: msg.to_pydantic() for msg in results}
            return [result_dict[msg_id] for msg_id in message_ids]

    @enforce_types
    def create_message(self, pydantic_msg: PydanticMessage, actor: PydanticUser) -> PydanticMessage:
        """Create a new message."""
        with self.session_maker() as session:
            # Set the organization id of the Pydantic message
            pydantic_msg.organization_id = actor.organization_id
            msg_data = pydantic_msg.model_dump(to_orm=True)
            msg = MessageModel(**msg_data)
            msg.create(session, actor=actor)  # Persist to database
            return msg.to_pydantic()

    @enforce_types
    def create_many_messages(self, pydantic_msgs: List[PydanticMessage], actor: PydanticUser) -> List[PydanticMessage]:
        """Create multiple messages."""
        return [self.create_message(m, actor=actor) for m in pydantic_msgs]

    @enforce_types
    def update_message_by_id(self, message_id: str, message_update: MessageUpdate, actor: PydanticUser) -> PydanticMessage:
        """
        Updates an existing record in the database with values from the provided record object.
        """
        with self.session_maker() as session:
            # Fetch existing message from database
            message = MessageModel.read(
                db_session=session,
                identifier=message_id,
                actor=actor,
            )

            # Some safety checks specific to messages
            if message_update.tool_calls and message.role != MessageRole.assistant:
                raise ValueError(
                    f"Tool calls {message_update.tool_calls} can only be added to assistant messages. Message {message_id} has role {message.role}."
                )
            if message_update.tool_call_id and message.role != MessageRole.tool:
                raise ValueError(
                    f"Tool call IDs {message_update.tool_call_id} can only be added to tool messages. Message {message_id} has role {message.role}."
                )

            # get update dictionary
            update_data = message_update.model_dump(to_orm=True, exclude_unset=True, exclude_none=True)
            # Remove redundant update fields
            update_data = {key: value for key, value in update_data.items() if getattr(message, key) != value}

            for key, value in update_data.items():
                setattr(message, key, value)
            message.update(db_session=session, actor=actor)

            return message.to_pydantic()

    @enforce_types
    def delete_message_by_id(self, message_id: str, actor: PydanticUser) -> bool:
        """Delete a message."""
        with self.session_maker() as session:
            try:
                msg = MessageModel.read(
                    db_session=session,
                    identifier=message_id,
                    actor=actor,
                )
                msg.hard_delete(session, actor=actor)
            except NoResultFound:
                raise ValueError(f"Message with id {message_id} not found.")

    @enforce_types
    def size(
        self,
        actor: PydanticUser,
        role: Optional[MessageRole] = None,
        agent_id: Optional[str] = None,
    ) -> int:
        """Get the total count of messages with optional filters.

        Args:
            actor: The user requesting the count
            role: The role of the message
        """
        with self.session_maker() as session:
            return MessageModel.size(db_session=session, actor=actor, role=role, agent_id=agent_id)

    @enforce_types
    def list_user_messages_for_agent(
        self,
        agent_id: str,
        actor: PydanticUser,
        after: Optional[str] = None,
        before: Optional[str] = None,
        query_text: Optional[str] = None,
        limit: Optional[int] = 50,
        ascending: bool = True,
    ) -> List[PydanticMessage]:
        return self.list_messages_for_agent(
            agent_id=agent_id,
            actor=actor,
            after=after,
            before=before,
            query_text=query_text,
            role=MessageRole.user,
            limit=limit,
            ascending=ascending,
        )

    @enforce_types
    def list_messages_for_agent(
        self,
        agent_id: str,
        actor: PydanticUser,
        after: Optional[str] = None,
        before: Optional[str] = None,
        query_text: Optional[str] = None,
        role: Optional[MessageRole] = None,  # New parameter for filtering by role
        limit: Optional[int] = 50,
        ascending: bool = True,
    ) -> List[PydanticMessage]:
        """
        Most performant query to list messages for an agent by directly querying the Message table.

        This function filters by the agent_id (leveraging the index on messages.agent_id)
        and applies efficient pagination using (created_at, id) as the cursor.
        If query_text is provided, it will filter messages whose text content partially matches the query.
        If role is provided, it will filter messages by the specified role.

        Args:
            agent_id: The ID of the agent whose messages are queried.
            actor: The user performing the action (used for permission checks).
            after: A message ID; if provided, only messages *after* this message (per sort order) are returned.
            before: A message ID; if provided, only messages *before* this message are returned.
            query_text: Optional string to partially match the message text content.
            role: Optional MessageRole to filter messages by role.
            limit: Maximum number of messages to return.
            ascending: If True, sort by (created_at, id) ascending; if False, sort descending.

        Returns:
            List[PydanticMessage]: A list of messages (converted via .to_pydantic()).

        Raises:
            NoResultFound: If the provided after/before message IDs do not exist.
        """
        with self.session_maker() as session:
            # Permission check: raise if the agent doesn't exist or actor is not allowed.
            AgentModel.read(db_session=session, identifier=agent_id, actor=actor)

            # Build a query that directly filters the Message table by agent_id.
            query = session.query(MessageModel).filter(MessageModel.agent_id == agent_id)

            # If query_text is provided, filter messages by partial match on text.
            if query_text:
                query = query.filter(MessageModel.text.ilike(f"%{query_text}%"))

            # If role is provided, filter messages by role.
            if role:
                query = query.filter(MessageModel.role == role.value)  # Enum.value ensures comparison is against the string value

            # Apply 'after' pagination if specified.
            if after:
                after_ref = session.query(MessageModel.created_at, MessageModel.id).filter(MessageModel.id == after).limit(1).one_or_none()
                if not after_ref:
                    raise NoResultFound(f"No message found with id '{after}' for agent '{agent_id}'.")
                query = query.filter(
                    or_(
                        MessageModel.created_at > after_ref.created_at,
                        and_(
                            MessageModel.created_at == after_ref.created_at,
                            MessageModel.id > after_ref.id,
                        ),
                    )
                )

            # Apply 'before' pagination if specified.
            if before:
                before_ref = (
                    session.query(MessageModel.created_at, MessageModel.id).filter(MessageModel.id == before).limit(1).one_or_none()
                )
                if not before_ref:
                    raise NoResultFound(f"No message found with id '{before}' for agent '{agent_id}'.")
                query = query.filter(
                    or_(
                        MessageModel.created_at < before_ref.created_at,
                        and_(
                            MessageModel.created_at == before_ref.created_at,
                            MessageModel.id < before_ref.id,
                        ),
                    )
                )

            # Apply ordering based on the ascending flag.
            if ascending:
                query = query.order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
            else:
                query = query.order_by(MessageModel.created_at.desc(), MessageModel.id.desc())

            # Limit the number of results.
            query = query.limit(limit)

            # Execute and convert each Message to its Pydantic representation.
            results = query.all()
            return [msg.to_pydantic() for msg in results]
