from logging import Logger
from typing import Optional

from letta.schemas.user import User
from letta.services.sandbox_config_manager import SandboxConfigManager
from letta.settings import tool_settings


def get_composio_api_key(actor: User, logger: Logger) -> Optional[str]:
    api_keys = SandboxConfigManager().list_sandbox_env_vars_by_key(key="COMPOSIO_API_KEY", actor=actor)
    if not api_keys:
        logger.warning(f"No API keys found for Composio. Defaulting to the environment variable...")
        if tool_settings.composio_api_key:
            return tool_settings.composio_api_key
        else:
            return None
    else:
        # TODO: Add more protections around this
        # Ideally, not tied to a specific sandbox, but for now we just get the first one
        # Theoretically possible for someone to have different composio api keys per sandbox
        return api_keys[0].value
