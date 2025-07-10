import ssl
import aiohttp
import asyncio
from typing import Optional, Tuple

from mcp.client import sse

class StreamReader:
    def __init__(self, response: aiohttp.ClientResponse):
        self._response = response

    async def __anext__(self) -> str:
        async for line in self._response.content:
            yield line.decode("utf-8")

    async def close(self):
        await self._response.release()

class StreamWriter:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def write(self, *args, **kwargs):
        pass  # unused in this case

    async def close(self):
        await self._session.close()

# ✅ Monkey-patched sse_client with optional SSL context
async def patched_sse_client(url: str, ssl_context: Optional[ssl.SSLContext] = None) -> Tuple[StreamReader, StreamWriter]:
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    session = aiohttp.ClientSession(connector=connector)
    resp = await session.get(url)
    reader = StreamReader(resp)
    writer = StreamWriter(session)
    return reader, writer

# ✅ Monkey-patch
sse.sse_client = patched_sse_client

# ✅ Main function to use the patched client
async def main():
    # Option 1: Bypass SSL verification for localhost dev
    # ssl_ctx = ssl._create_unverified_context()

    # Option 2 (preferred): Use mkcert's trusted root CA
    import os
    ca_path = os.path.expanduser("~/AppData/Local/mkcert/rootCA.pem")  
    ssl_ctx = ssl.create_default_context(cafile=ca_path)

    reader, writer = await sse.sse_client("https://localhost:8003/mcp-server/sse/", ssl_context=ssl_ctx)
    print("✅ Connected to MCP server with SSL!")
    # ✅ Cleanly close session
    await writer.close()
    await reader.close()

asyncio.run(main())
