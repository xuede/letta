import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

from letta.functions.mcp_client.base_client import BaseMCPClient
from letta.functions.mcp_client.types import SSEServerConfig
from letta.log import get_logger

# see: https://modelcontextprotocol.io/quickstart/user
MCP_CONFIG_TOPLEVEL_KEY = "mcpServers"

logger = get_logger(__name__)


class SSEMCPClient(BaseMCPClient):
    def _initialize_connection(self, server_config: SSEServerConfig, timeout: float) -> bool:
        try:
            sse_cm = sse_client(url=server_config.server_url)
            sse_transport = self.loop.run_until_complete(asyncio.wait_for(sse_cm.__aenter__(), timeout=timeout))
            self.stdio, self.write = sse_transport
            self.cleanup_funcs.append(lambda: self.loop.run_until_complete(sse_cm.__aexit__(None, None, None)))

            session_cm = ClientSession(self.stdio, self.write)
            self.session = self.loop.run_until_complete(asyncio.wait_for(session_cm.__aenter__(), timeout=timeout))
            self.cleanup_funcs.append(lambda: self.loop.run_until_complete(session_cm.__aexit__(None, None, None)))
            return True
        except asyncio.TimeoutError:
            logger.error(f"Timed out while establishing SSE connection (timeout={timeout}s).")
            return False
        except Exception:
            logger.exception("Exception occurred while initializing SSE client session.")
            return False
