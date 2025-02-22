from typing import List, Optional

from letta.orm.errors import NoResultFound
from letta.orm.organization import Organization as OrganizationModel
from letta.orm.user import User as UserModel
from letta.schemas.user import User as PydanticUser
from letta.schemas.user import UserUpdate
from letta.services.organization_manager import OrganizationManager
from letta.utils import enforce_types


class UserManager:
    """Manager class to handle business logic related to Users."""

    DEFAULT_USER_NAME = "default_user"
    DEFAULT_USER_ID = "user-00000000-0000-4000-8000-000000000000"

    def __init__(self):
        # Fetching the db_context similarly as in OrganizationManager
        from letta.server.db import db_context

        self.session_maker = db_context

    @enforce_types
    def create_default_user(self, org_id: str = OrganizationManager.DEFAULT_ORG_ID) -> PydanticUser:
        """Create the default user."""
        with self.session_maker() as session:
            # Make sure the org id exists
            try:
                OrganizationModel.read(db_session=session, identifier=org_id)
            except NoResultFound:
                raise ValueError(f"No organization with {org_id} exists in the organization table.")

            # Try to retrieve the user
            try:
                user = UserModel.read(db_session=session, identifier=self.DEFAULT_USER_ID)
            except NoResultFound:
                # If it doesn't exist, make it
                user = UserModel(id=self.DEFAULT_USER_ID, name=self.DEFAULT_USER_NAME, organization_id=org_id)
                user.create(session)

            return user.to_pydantic()

    @enforce_types
    def create_user(self, pydantic_user: PydanticUser) -> PydanticUser:
        """Create a new user if it doesn't already exist."""
        with self.session_maker() as session:
            new_user = UserModel(**pydantic_user.model_dump(to_orm=True))
            new_user.create(session)
            return new_user.to_pydantic()

    @enforce_types
    def update_user(self, user_update: UserUpdate) -> PydanticUser:
        """Update user details."""
        with self.session_maker() as session:
            # Retrieve the existing user by ID
            existing_user = UserModel.read(db_session=session, identifier=user_update.id)

            # Update only the fields that are provided in UserUpdate
            update_data = user_update.model_dump(to_orm=True, exclude_unset=True, exclude_none=True)
            for key, value in update_data.items():
                setattr(existing_user, key, value)

            # Commit the updated user
            existing_user.update(session)
            return existing_user.to_pydantic()

    @enforce_types
    def delete_user_by_id(self, user_id: str):
        """Delete a user and their associated records (agents, sources, mappings)."""
        with self.session_maker() as session:
            # Delete from user table
            user = UserModel.read(db_session=session, identifier=user_id)
            user.hard_delete(session)

            session.commit()

    @enforce_types
    def get_user_by_id(self, user_id: str) -> PydanticUser:
        """Fetch a user by ID."""
        with self.session_maker() as session:
            user = UserModel.read(db_session=session, identifier=user_id)
            return user.to_pydantic()

    @enforce_types
    def get_default_user(self) -> PydanticUser:
        """Fetch the default user."""
        return self.get_user_by_id(self.DEFAULT_USER_ID)

    @enforce_types
    def get_user_or_default(self, user_id: Optional[str] = None):
        """Fetch the user or default user."""
        if not user_id:
            return self.get_default_user()

        try:
            return self.get_user_by_id(user_id=user_id)
        except NoResultFound:
            return self.get_default_user()

    @enforce_types
    def list_users(self, after: Optional[str] = None, limit: Optional[int] = 50) -> List[PydanticUser]:
        """List all users with optional pagination."""
        with self.session_maker() as session:
            users = UserModel.list(
                db_session=session,
                after=after,
                limit=limit,
            )
            return [user.to_pydantic() for user in users]
