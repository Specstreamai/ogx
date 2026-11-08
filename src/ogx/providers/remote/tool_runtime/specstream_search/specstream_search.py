# Copyright (c) The OGX Contributors.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import json
from typing import Any
from urllib.parse import urlparse

import httpx2

from ogx.core.request_headers import NeedsRequestProviderData
from ogx_api import (
    URL,
    ListToolDefsResponse,
    ToolDef,
    ToolGroup,
    ToolGroupsProtocolPrivate,
    ToolInvocationResult,
    ToolRuntime,
)

from .config import SpecStreamSearchToolConfig


class SpecStreamSearchToolRuntimeImpl(ToolGroupsProtocolPrivate, ToolRuntime, NeedsRequestProviderData):
    """Tool runtime for performing web searches using the SpecStream Search API."""

    _SEARCH_URL = "https://api.specstream.ai/v1/web-search"
    _CONTEXT_SIZE_TO_COUNT = {"low": 3, "medium": 5, "high": 10}
    _MAX_RESULTS = 10
    # When a domain filter is set, ask for more sources and keep the matching ones.
    _DOMAIN_FILTER_POOL = 50

    def __init__(self, config: SpecStreamSearchToolConfig):
        self.config = config
        self._client: httpx2.AsyncClient | None = None

    async def initialize(self) -> None:
        self._client = httpx2.AsyncClient(timeout=self.config.to_httpx_timeout())

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def register_toolgroup(self, toolgroup: ToolGroup) -> None:
        pass

    async def unregister_toolgroup(self, toolgroup_id: str) -> None:
        return

    @staticmethod
    def _host_allowed(url: str | None, allowed_domains: list[str]) -> bool:
        host = (urlparse(url).hostname or "").lower() if url else ""
        return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed_domains)

    def _get_api_key(self) -> str | None:
        api_key = self.config.api_key.get_secret_value() if self.config.api_key else None

        provider_data = self.get_request_provider_data()
        if provider_data and provider_data.specstream_search_api_key:
            api_key = str(provider_data.specstream_search_api_key.get_secret_value())

        return api_key

    async def list_runtime_tools(
        self,
        tool_group_id: str | None = None,
        mcp_endpoint: URL | None = None,
        authorization: str | None = None,
    ) -> ListToolDefsResponse:
        return ListToolDefsResponse(
            data=[
                ToolDef(
                    name="web_search",
                    description="Search the web for information",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The query to search for",
                            }
                        },
                        "required": ["query"],
                    },
                )
            ]
        )

    async def invoke_tool(
        self, tool_name: str, kwargs: dict[str, Any], authorization: str | None = None
    ) -> ToolInvocationResult:
        api_key = self._get_api_key()
        query = kwargs["query"]

        allowed_domains = kwargs.get("allowed_domains")

        result_limit = self.config.max_results
        search_context_size = kwargs.get("search_context_size")
        if search_context_size and search_context_size in self._CONTEXT_SIZE_TO_COUNT:
            result_limit = self._CONTEXT_SIZE_TO_COUNT[search_context_size]
        result_limit = max(1, min(result_limit, self._MAX_RESULTS))

        params: dict[str, Any] = {"q": query, "num": self._DOMAIN_FILTER_POOL if allowed_domains else result_limit}

        if self._client is None:
            raise RuntimeError("Failed to invoke tool: provider not initialized")
        headers = {"User-Agent": "ogx"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = await self._client.get(
            self._SEARCH_URL,
            params=params,
            headers=headers,
        )
        if response.status_code == 401:
            # A missing or invalid key. Return the reason as tool content so the
            # executor forwards it to the model rather than a generic failure.
            message = "Failed to query SpecStream Search: the API key is missing or invalid."
            return ToolInvocationResult(
                content=json.dumps({"error": message}),
                error_code=401,
                error_message=message,
                metadata={"query": kwargs["query"], "sources": []},
            )
        response.raise_for_status()

        response_json = response.json()
        sources_found = (response_json.get("data") or {}).get("sources") or []
        results = []
        sources = []
        for r in sources_found:
            url = r.get("url")
            if allowed_domains and not self._host_allowed(url, allowed_domains):
                continue
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": url,
                    "content": r.get("snippet", ""),
                }
            )
            if url:
                sources.append({"url": url})
            if len(results) >= result_limit:
                break

        return ToolInvocationResult(
            content=json.dumps({"query": kwargs["query"], "results": results}),
            metadata={"query": kwargs["query"], "sources": sources},
        )
