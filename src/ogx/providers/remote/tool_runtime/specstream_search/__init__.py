# Copyright (c) The OGX Contributors.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from pydantic import BaseModel, SecretStr

from .config import SpecStreamSearchToolConfig
from .specstream_search import SpecStreamSearchToolRuntimeImpl


class SpecStreamSearchToolProviderDataValidator(BaseModel):
    """Validator for SpecStream Search tool provider data requiring a SpecStream API key."""

    specstream_search_api_key: SecretStr


async def get_adapter_impl(config: SpecStreamSearchToolConfig, _deps):
    impl = SpecStreamSearchToolRuntimeImpl(config)
    await impl.initialize()
    return impl
