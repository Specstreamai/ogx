# Copyright (c) The OGX Contributors.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import json
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from pydantic import SecretStr

from ogx.providers.remote.tool_runtime.specstream_search.config import SpecStreamSearchToolConfig
from ogx.providers.remote.tool_runtime.specstream_search.specstream_search import SpecStreamSearchToolRuntimeImpl


@pytest.fixture
def specstream_search():
    impl = SpecStreamSearchToolRuntimeImpl(SpecStreamSearchToolConfig(api_key="test-key", max_results=3))
    impl._client = MagicMock(spec=httpx2.AsyncClient)
    impl.get_request_provider_data = MagicMock(return_value=None)
    return impl


def _response(count: int = 1, host: str = "example.com") -> httpx2.Response:
    # Shape of GET /v1/web-search: results sit in the `data` envelope under `sources`.
    return httpx2.Response(
        200,
        json={
            "data": {
                "kind": "list",
                "sources": [
                    {
                        "title": f"Test Result {i}",
                        "url": f"https://{host}/{i}",
                        "snippet": f"A test result snippet {i}",
                    }
                    for i in range(count)
                ],
            }
        },
        request=httpx2.Request("GET", SpecStreamSearchToolRuntimeImpl._SEARCH_URL),
    )


async def test_invoke_sends_get_with_query_and_config_default(specstream_search):
    specstream_search._client.get = AsyncMock(return_value=_response())
    await specstream_search.invoke_tool("web_search", {"query": "test query"})
    call = specstream_search._client.get.call_args
    assert call.args[0] == "https://api.specstream.ai/v1/web-search"
    assert call.kwargs["params"] == {"q": "test query", "num": 3}


async def test_api_key_sent_as_bearer_header(specstream_search):
    specstream_search._client.get = AsyncMock(return_value=_response())
    await specstream_search.invoke_tool("web_search", {"query": "q"})
    call = specstream_search._client.get.call_args
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert "test-key" not in str(call.kwargs["params"])


async def test_missing_api_key_sends_no_auth_header():
    impl = SpecStreamSearchToolRuntimeImpl(SpecStreamSearchToolConfig())
    impl._client = MagicMock(spec=httpx2.AsyncClient)
    impl._client.get = AsyncMock(return_value=_response())
    impl.get_request_provider_data = MagicMock(return_value=None)
    await impl.invoke_tool("web_search", {"query": "q"})
    assert "Authorization" not in impl._client.get.call_args.kwargs["headers"]


async def test_provider_data_overrides_config_api_key(specstream_search):
    specstream_search.get_request_provider_data = MagicMock(
        return_value=MagicMock(specstream_search_api_key=SecretStr("request-key"))
    )
    specstream_search._client.get = AsyncMock(return_value=_response())
    await specstream_search.invoke_tool("web_search", {"query": "q"})
    assert specstream_search._client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer request-key"


@pytest.mark.parametrize("size,expected", [("low", 3), ("medium", 5), ("high", 10)])
async def test_search_context_size_mapping(specstream_search, size, expected):
    specstream_search._client.get = AsyncMock(return_value=_response())
    await specstream_search.invoke_tool("web_search", {"query": "q", "search_context_size": size})
    assert specstream_search._client.get.call_args.kwargs["params"]["num"] == expected


async def test_max_results_is_clamped_to_api_ceiling():
    impl = SpecStreamSearchToolRuntimeImpl(SpecStreamSearchToolConfig(api_key="k", max_results=50))
    impl._client = MagicMock(spec=httpx2.AsyncClient)
    impl._client.get = AsyncMock(return_value=_response())
    impl.get_request_provider_data = MagicMock(return_value=None)
    await impl.invoke_tool("web_search", {"query": "q"})
    assert impl._client.get.call_args.kwargs["params"]["num"] == 10


async def test_results_map_url_title_and_snippet_and_are_trimmed(specstream_search):
    specstream_search._client.get = AsyncMock(return_value=_response(count=8))
    result = await specstream_search.invoke_tool("web_search", {"query": "q"})
    content = json.loads(result.content)
    assert content["query"] == "q"
    assert len(content["results"]) == 3
    assert content["results"][0] == {
        "title": "Test Result 0",
        "url": "https://example.com/0",
        "content": "A test result snippet 0",
    }
    assert result.metadata["sources"][0] == {"url": "https://example.com/0"}


async def test_allowed_domains_keep_only_matching_hosts(specstream_search):
    mixed = _response(count=2, host="docs.example.com").json()
    mixed["data"]["sources"] += _response(count=2, host="other.org").json()["data"]["sources"]
    specstream_search._client.get = AsyncMock(
        return_value=httpx2.Response(200, json=mixed, request=httpx2.Request("GET", "https://api.specstream.ai"))
    )
    result = await specstream_search.invoke_tool("web_search", {"query": "q", "allowed_domains": ["example.com"]})
    urls = [r["url"] for r in json.loads(result.content)["results"]]
    assert urls == ["https://docs.example.com/0", "https://docs.example.com/1"]
    assert specstream_search._client.get.call_args.kwargs["params"]["num"] > 10


async def test_401_returns_graceful_tool_error(specstream_search):
    specstream_search._client.get = AsyncMock(
        return_value=httpx2.Response(401, request=httpx2.Request("GET", "https://api.specstream.ai"))
    )
    result = await specstream_search.invoke_tool("web_search", {"query": "q"})
    assert result.error_code == 401
    assert "missing or invalid" in result.error_message


async def test_other_http_errors_raise(specstream_search):
    specstream_search._client.get = AsyncMock(
        return_value=httpx2.Response(500, request=httpx2.Request("GET", "https://api.specstream.ai"))
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await specstream_search.invoke_tool("web_search", {"query": "q"})
