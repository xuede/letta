from typing import Dict, List, Optional

from letta.constants import LETTA_TOOL_EXECUTION_DIR
from letta.log import get_logger
from letta.orm.errors import NoResultFound
from letta.orm.sandbox_config import SandboxConfig as SandboxConfigModel
from letta.orm.sandbox_config import SandboxEnvironmentVariable as SandboxEnvVarModel
from letta.otel.tracing import trace_method
from letta.schemas.environment_variables import SandboxEnvironmentVariable as PydanticEnvVar
from letta.schemas.environment_variables import SandboxEnvironmentVariableCreate, SandboxEnvironmentVariableUpdate
from letta.schemas.sandbox_config import LocalSandboxConfig
from letta.schemas.sandbox_config import SandboxConfig as PydanticSandboxConfig
from letta.schemas.sandbox_config import SandboxConfigCreate, SandboxConfigUpdate, SandboxType
from letta.schemas.user import User as PydanticUser
from letta.server.db import db_registry
from letta.utils import enforce_types, printd

logger = get_logger(__name__)


class SandboxConfigManager:
    """Manager class to handle business logic related to SandboxConfig and SandboxEnvironmentVariable."""

    @enforce_types
    @trace_method
    def get_or_create_default_sandbox_config(self, sandbox_type: SandboxType, actor: PydanticUser) -> PydanticSandboxConfig:
        sandbox_config = self.get_sandbox_config_by_type(sandbox_type, actor=actor)
        if not sandbox_config:
            logger.debug(f"Creating new sandbox config of type {sandbox_type}, none found for organization {actor.organization_id}.")

            # TODO: Add more sandbox types later
            if sandbox_type == SandboxType.E2B:
                default_config = {}  # Empty
            else:
                # TODO: May want to move this to environment variables v.s. persisting in database
                default_local_sandbox_path = LETTA_TOOL_EXECUTION_DIR
                default_config = LocalSandboxConfig(sandbox_dir=default_local_sandbox_path).model_dump(exclude_none=True)

            sandbox_config = self.create_or_update_sandbox_config(SandboxConfigCreate(config=default_config), actor=actor)
        return sandbox_config

    @enforce_types
    @trace_method
    def create_or_update_sandbox_config(self, sandbox_config_create: SandboxConfigCreate, actor: PydanticUser) -> PydanticSandboxConfig:
        """Create or update a sandbox configuration based on the PydanticSandboxConfig schema."""
        config = sandbox_config_create.config
        sandbox_type = config.type
        sandbox_config = PydanticSandboxConfig(
            type=sandbox_type, config=config.model_dump(exclude_none=True), organization_id=actor.organization_id
        )

        # Attempt to retrieve the existing sandbox configuration by type within the organization
        db_sandbox = self.get_sandbox_config_by_type(sandbox_config.type, actor=actor)
        if db_sandbox:
            # Prepare the update data, excluding fields that should not be reset
            update_data = sandbox_config.model_dump(exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(db_sandbox, key) != value}

            # If there are changes, update the sandbox configuration
            if update_data:
                db_sandbox = self.update_sandbox_config(db_sandbox.id, SandboxConfigUpdate(**update_data), actor)
            else:
                printd(
                    f"`create_or_update_sandbox_config` was called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"type={sandbox_config.type}, but found existing configuration with nothing to update."
                )

            return db_sandbox
        else:
            # If the sandbox configuration doesn't exist, create a new one
            with db_registry.session() as session:
                db_sandbox = SandboxConfigModel(**sandbox_config.model_dump(exclude_none=True))
                db_sandbox.create(session, actor=actor)
                return db_sandbox.to_pydantic()

    @enforce_types
    @trace_method
    async def get_or_create_default_sandbox_config_async(self, sandbox_type: SandboxType, actor: PydanticUser) -> PydanticSandboxConfig:
        sandbox_config = await self.get_sandbox_config_by_type_async(sandbox_type, actor=actor)
        if not sandbox_config:
            logger.debug(f"Creating new sandbox config of type {sandbox_type}, none found for organization {actor.organization_id}.")

            # TODO: Add more sandbox types later
            if sandbox_type == SandboxType.E2B:
                default_config = {}  # Empty
            else:
                # TODO: May want to move this to environment variables v.s. persisting in database
                default_local_sandbox_path = LETTA_TOOL_EXECUTION_DIR
                default_config = LocalSandboxConfig(sandbox_dir=default_local_sandbox_path).model_dump(exclude_none=True)

            sandbox_config = await self.create_or_update_sandbox_config_async(SandboxConfigCreate(config=default_config), actor=actor)
        return sandbox_config

    @enforce_types
    @trace_method
    async def create_or_update_sandbox_config_async(
        self, sandbox_config_create: SandboxConfigCreate, actor: PydanticUser
    ) -> PydanticSandboxConfig:
        """Create or update a sandbox configuration based on the PydanticSandboxConfig schema."""
        config = sandbox_config_create.config
        sandbox_type = config.type
        sandbox_config = PydanticSandboxConfig(
            type=sandbox_type, config=config.model_dump(exclude_none=True), organization_id=actor.organization_id
        )

        # Attempt to retrieve the existing sandbox configuration by type within the organization
        db_sandbox = await self.get_sandbox_config_by_type_async(sandbox_config.type, actor=actor)
        if db_sandbox:
            # Prepare the update data, excluding fields that should not be reset
            update_data = sandbox_config.model_dump(exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(db_sandbox, key) != value}

            # If there are changes, update the sandbox configuration
            if update_data:
                db_sandbox = await self.update_sandbox_config_async(db_sandbox.id, SandboxConfigUpdate(**update_data), actor)
            else:
                printd(
                    f"`create_or_update_sandbox_config` was called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"type={sandbox_config.type}, but found existing configuration with nothing to update."
                )

            return db_sandbox
        else:
            # If the sandbox configuration doesn't exist, create a new one
            async with db_registry.async_session() as session:
                db_sandbox = SandboxConfigModel(**sandbox_config.model_dump(exclude_none=True))
                await db_sandbox.create_async(session, actor=actor)
                return db_sandbox.to_pydantic()

    @enforce_types
    @trace_method
    def update_sandbox_config(
        self, sandbox_config_id: str, sandbox_update: SandboxConfigUpdate, actor: PydanticUser
    ) -> PydanticSandboxConfig:
        """Update an existing sandbox configuration."""
        with db_registry.session() as session:
            sandbox = SandboxConfigModel.read(db_session=session, identifier=sandbox_config_id, actor=actor)
            # We need to check that the sandbox_update provided is the same type as the original sandbox
            if sandbox.type != sandbox_update.config.type:
                raise ValueError(
                    f"Mismatched type for sandbox config update: tried to update sandbox_config of type {sandbox.type} with config of type {sandbox_update.config.type}"
                )

            update_data = sandbox_update.model_dump(exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(sandbox, key) != value}

            if update_data:
                for key, value in update_data.items():
                    setattr(sandbox, key, value)
                sandbox.update(db_session=session, actor=actor)
            else:
                printd(
                    f"`update_sandbox_config` called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"name={sandbox.type}, but nothing to update."
                )
            return sandbox.to_pydantic()

    @enforce_types
    @trace_method
    async def update_sandbox_config_async(
        self, sandbox_config_id: str, sandbox_update: SandboxConfigUpdate, actor: PydanticUser
    ) -> PydanticSandboxConfig:
        """Update an existing sandbox configuration."""
        async with db_registry.async_session() as session:
            sandbox = await SandboxConfigModel.read_async(db_session=session, identifier=sandbox_config_id, actor=actor)
            # We need to check that the sandbox_update provided is the same type as the original sandbox
            if sandbox.type != sandbox_update.config.type:
                raise ValueError(
                    f"Mismatched type for sandbox config update: tried to update sandbox_config of type {sandbox.type} with config of type {sandbox_update.config.type}"
                )

            update_data = sandbox_update.model_dump(exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(sandbox, key) != value}

            if update_data:
                for key, value in update_data.items():
                    setattr(sandbox, key, value)
                await sandbox.update_async(db_session=session, actor=actor)
            else:
                printd(
                    f"`update_sandbox_config` called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"name={sandbox.type}, but nothing to update."
                )
            return sandbox.to_pydantic()

    @enforce_types
    @trace_method
    def delete_sandbox_config(self, sandbox_config_id: str, actor: PydanticUser) -> PydanticSandboxConfig:
        """Delete a sandbox configuration by its ID."""
        with db_registry.session() as session:
            sandbox = SandboxConfigModel.read(db_session=session, identifier=sandbox_config_id, actor=actor)
            sandbox.hard_delete(db_session=session, actor=actor)
            return sandbox.to_pydantic()

    @enforce_types
    @trace_method
    async def delete_sandbox_config_async(self, sandbox_config_id: str, actor: PydanticUser) -> PydanticSandboxConfig:
        """Delete a sandbox configuration by its ID."""
        async with db_registry.async_session() as session:
            sandbox = await SandboxConfigModel.read_async(db_session=session, identifier=sandbox_config_id, actor=actor)
            await sandbox.hard_delete_async(db_session=session, actor=actor)
            return sandbox.to_pydantic()

    @enforce_types
    @trace_method
    def list_sandbox_configs(
        self,
        actor: PydanticUser,
        after: Optional[str] = None,
        limit: Optional[int] = 50,
        sandbox_type: Optional[SandboxType] = None,
    ) -> List[PydanticSandboxConfig]:
        """List all sandbox configurations with optional pagination."""
        kwargs = {"organization_id": actor.organization_id}
        if sandbox_type:
            kwargs.update({"type": sandbox_type})

        with db_registry.session() as session:
            sandboxes = SandboxConfigModel.list(db_session=session, after=after, limit=limit, **kwargs)
            return [sandbox.to_pydantic() for sandbox in sandboxes]

    @enforce_types
    @trace_method
    async def list_sandbox_configs_async(
        self,
        actor: PydanticUser,
        after: Optional[str] = None,
        limit: Optional[int] = 50,
        sandbox_type: Optional[SandboxType] = None,
    ) -> List[PydanticSandboxConfig]:
        """List all sandbox configurations with optional pagination."""
        kwargs = {"organization_id": actor.organization_id}
        if sandbox_type:
            kwargs.update({"type": sandbox_type})

        async with db_registry.async_session() as session:
            sandboxes = await SandboxConfigModel.list_async(db_session=session, after=after, limit=limit, **kwargs)
            return [sandbox.to_pydantic() for sandbox in sandboxes]

    @enforce_types
    @trace_method
    def get_sandbox_config_by_id(self, sandbox_config_id: str, actor: Optional[PydanticUser] = None) -> Optional[PydanticSandboxConfig]:
        """Retrieve a sandbox configuration by its ID."""
        with db_registry.session() as session:
            try:
                sandbox = SandboxConfigModel.read(db_session=session, identifier=sandbox_config_id, actor=actor)
                return sandbox.to_pydantic()
            except NoResultFound:
                return None

    @enforce_types
    @trace_method
    def get_sandbox_config_by_type(self, type: SandboxType, actor: Optional[PydanticUser] = None) -> Optional[PydanticSandboxConfig]:
        """Retrieve a sandbox config by its type."""
        with db_registry.session() as session:
            try:
                sandboxes = SandboxConfigModel.list(
                    db_session=session,
                    type=type,
                    organization_id=actor.organization_id,
                    limit=1,
                )
                if sandboxes:
                    return sandboxes[0].to_pydantic()
                return None
            except NoResultFound:
                return None

    @enforce_types
    @trace_method
    async def get_sandbox_config_by_type_async(
        self, type: SandboxType, actor: Optional[PydanticUser] = None
    ) -> Optional[PydanticSandboxConfig]:
        """Retrieve a sandbox config by its type."""
        async with db_registry.async_session() as session:
            try:
                sandboxes = await SandboxConfigModel.list_async(
                    db_session=session,
                    type=type,
                    organization_id=actor.organization_id,
                    limit=1,
                )
                if sandboxes:
                    return sandboxes[0].to_pydantic()
                return None
            except NoResultFound:
                return None

    @enforce_types
    @trace_method
    def create_sandbox_env_var(
        self, env_var_create: SandboxEnvironmentVariableCreate, sandbox_config_id: str, actor: PydanticUser
    ) -> PydanticEnvVar:
        """Create a new sandbox environment variable."""
        env_var = PydanticEnvVar(**env_var_create.model_dump(), sandbox_config_id=sandbox_config_id, organization_id=actor.organization_id)

        db_env_var = self.get_sandbox_env_var_by_key_and_sandbox_config_id(env_var.key, env_var.sandbox_config_id, actor=actor)
        if db_env_var:
            update_data = env_var.model_dump(exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(db_env_var, key) != value}
            # If there are changes, update the environment variable
            if update_data:
                db_env_var = self.update_sandbox_env_var(db_env_var.id, SandboxEnvironmentVariableUpdate(**update_data), actor)
            else:
                printd(
                    f"`create_or_update_sandbox_env_var` was called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"key={env_var.key}, but found existing variable with nothing to update."
                )

            return db_env_var
        else:
            with db_registry.session() as session:
                env_var = SandboxEnvVarModel(**env_var.model_dump(to_orm=True, exclude_none=True))
                env_var.create(session, actor=actor)
            return env_var.to_pydantic()

    @enforce_types
    @trace_method
    async def create_sandbox_env_var_async(
        self, env_var_create: SandboxEnvironmentVariableCreate, sandbox_config_id: str, actor: PydanticUser
    ) -> PydanticEnvVar:
        """Create a new sandbox environment variable."""
        env_var = PydanticEnvVar(**env_var_create.model_dump(), sandbox_config_id=sandbox_config_id, organization_id=actor.organization_id)

        db_env_var = await self.get_sandbox_env_var_by_key_and_sandbox_config_id_async(env_var.key, env_var.sandbox_config_id, actor=actor)
        if db_env_var:
            update_data = env_var.model_dump(exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(db_env_var, key) != value}
            # If there are changes, update the environment variable
            if update_data:
                db_env_var = await self.update_sandbox_env_var_async(db_env_var.id, SandboxEnvironmentVariableUpdate(**update_data), actor)
            else:
                printd(
                    f"`create_or_update_sandbox_env_var` was called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"key={env_var.key}, but found existing variable with nothing to update."
                )

            return db_env_var
        else:
            async with db_registry.async_session() as session:
                env_var = SandboxEnvVarModel(**env_var.model_dump(to_orm=True, exclude_none=True))
                await env_var.create_async(session, actor=actor)
                return env_var.to_pydantic()

    @enforce_types
    @trace_method
    def update_sandbox_env_var(
        self, env_var_id: str, env_var_update: SandboxEnvironmentVariableUpdate, actor: PydanticUser
    ) -> PydanticEnvVar:
        """Update an existing sandbox environment variable."""
        with db_registry.session() as session:
            env_var = SandboxEnvVarModel.read(db_session=session, identifier=env_var_id, actor=actor)
            update_data = env_var_update.model_dump(to_orm=True, exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(env_var, key) != value}

            if update_data:
                for key, value in update_data.items():
                    setattr(env_var, key, value)
                env_var.update(db_session=session, actor=actor)
            else:
                printd(
                    f"`update_sandbox_env_var` called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"key={env_var.key}, but nothing to update."
                )
            return env_var.to_pydantic()

    @enforce_types
    @trace_method
    async def update_sandbox_env_var_async(
        self, env_var_id: str, env_var_update: SandboxEnvironmentVariableUpdate, actor: PydanticUser
    ) -> PydanticEnvVar:
        """Update an existing sandbox environment variable."""
        async with db_registry.async_session() as session:
            env_var = await SandboxEnvVarModel.read_async(db_session=session, identifier=env_var_id, actor=actor)
            update_data = env_var_update.model_dump(to_orm=True, exclude_unset=True, exclude_none=True)
            update_data = {key: value for key, value in update_data.items() if getattr(env_var, key) != value}

            if update_data:
                for key, value in update_data.items():
                    setattr(env_var, key, value)
                await env_var.update_async(db_session=session, actor=actor)
            else:
                printd(
                    f"`update_sandbox_env_var` called with user_id={actor.id}, organization_id={actor.organization_id}, "
                    f"key={env_var.key}, but nothing to update."
                )
            return env_var.to_pydantic()

    @enforce_types
    @trace_method
    def delete_sandbox_env_var(self, env_var_id: str, actor: PydanticUser) -> PydanticEnvVar:
        """Delete a sandbox environment variable by its ID."""
        with db_registry.session() as session:
            env_var = SandboxEnvVarModel.read(db_session=session, identifier=env_var_id, actor=actor)
            env_var.hard_delete(db_session=session, actor=actor)
            return env_var.to_pydantic()

    @enforce_types
    @trace_method
    async def delete_sandbox_env_var_async(self, env_var_id: str, actor: PydanticUser) -> PydanticEnvVar:
        """Delete a sandbox environment variable by its ID."""
        async with db_registry.async_session() as session:
            env_var = await SandboxEnvVarModel.read_async(db_session=session, identifier=env_var_id, actor=actor)
            await env_var.hard_delete_async(db_session=session, actor=actor)
            return env_var.to_pydantic()

    @enforce_types
    @trace_method
    def list_sandbox_env_vars(
        self,
        sandbox_config_id: str,
        actor: PydanticUser,
        after: Optional[str] = None,
        limit: Optional[int] = 50,
    ) -> List[PydanticEnvVar]:
        """List all sandbox environment variables with optional pagination."""
        with db_registry.session() as session:
            env_vars = SandboxEnvVarModel.list(
                db_session=session,
                after=after,
                limit=limit,
                organization_id=actor.organization_id,
                sandbox_config_id=sandbox_config_id,
            )
            return [env_var.to_pydantic() for env_var in env_vars]

    @enforce_types
    @trace_method
    async def list_sandbox_env_vars_async(
        self,
        sandbox_config_id: str,
        actor: PydanticUser,
        after: Optional[str] = None,
        limit: Optional[int] = 50,
    ) -> List[PydanticEnvVar]:
        """List all sandbox environment variables with optional pagination."""
        async with db_registry.async_session() as session:
            env_vars = await SandboxEnvVarModel.list_async(
                db_session=session,
                after=after,
                limit=limit,
                organization_id=actor.organization_id,
                sandbox_config_id=sandbox_config_id,
            )
            return [env_var.to_pydantic() for env_var in env_vars]

    @enforce_types
    @trace_method
    def list_sandbox_env_vars_by_key(
        self, key: str, actor: PydanticUser, after: Optional[str] = None, limit: Optional[int] = 50
    ) -> List[PydanticEnvVar]:
        """List all sandbox environment variables with optional pagination."""
        with db_registry.session() as session:
            env_vars = SandboxEnvVarModel.list(
                db_session=session,
                after=after,
                limit=limit,
                organization_id=actor.organization_id,
                key=key,
            )
            return [env_var.to_pydantic() for env_var in env_vars]

    @enforce_types
    @trace_method
    async def list_sandbox_env_vars_by_key_async(
        self, key: str, actor: PydanticUser, after: Optional[str] = None, limit: Optional[int] = 50
    ) -> List[PydanticEnvVar]:
        """List all sandbox environment variables with optional pagination."""
        async with db_registry.async_session() as session:
            env_vars = await SandboxEnvVarModel.list_async(
                db_session=session,
                after=after,
                limit=limit,
                organization_id=actor.organization_id,
                key=key,
            )
            return [env_var.to_pydantic() for env_var in env_vars]

    @enforce_types
    @trace_method
    def get_sandbox_env_vars_as_dict(
        self, sandbox_config_id: str, actor: PydanticUser, after: Optional[str] = None, limit: Optional[int] = 50
    ) -> Dict[str, str]:
        env_vars = self.list_sandbox_env_vars(sandbox_config_id, actor, after, limit)
        result = {}
        for env_var in env_vars:
            result[env_var.key] = env_var.value
        return result

    @enforce_types
    @trace_method
    async def get_sandbox_env_vars_as_dict_async(
        self, sandbox_config_id: str, actor: PydanticUser, after: Optional[str] = None, limit: Optional[int] = 50
    ) -> Dict[str, str]:
        env_vars = await self.list_sandbox_env_vars_async(sandbox_config_id, actor, after, limit)
        result = {}
        for env_var in env_vars:
            result[env_var.key] = env_var.value
        return result

    @enforce_types
    @trace_method
    def get_sandbox_env_var_by_key_and_sandbox_config_id(
        self, key: str, sandbox_config_id: str, actor: Optional[PydanticUser] = None
    ) -> Optional[PydanticEnvVar]:
        """Retrieve a sandbox environment variable by its key and sandbox_config_id."""
        with db_registry.session() as session:
            try:
                env_var = SandboxEnvVarModel.list(
                    db_session=session,
                    key=key,
                    sandbox_config_id=sandbox_config_id,
                    organization_id=actor.organization_id,
                    limit=1,
                )
                if env_var:
                    return env_var[0].to_pydantic()
                return None
            except NoResultFound:
                return None

    @enforce_types
    @trace_method
    async def get_sandbox_env_var_by_key_and_sandbox_config_id_async(
        self, key: str, sandbox_config_id: str, actor: Optional[PydanticUser] = None
    ) -> Optional[PydanticEnvVar]:
        """Retrieve a sandbox environment variable by its key and sandbox_config_id."""
        async with db_registry.async_session() as session:
            try:
                env_var = await SandboxEnvVarModel.list_async(
                    db_session=session,
                    key=key,
                    sandbox_config_id=sandbox_config_id,
                    organization_id=actor.organization_id,
                    limit=1,
                )
                if env_var:
                    return env_var[0].to_pydantic()
                return None
            except NoResultFound:
                return None
