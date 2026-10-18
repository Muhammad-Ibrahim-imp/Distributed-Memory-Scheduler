"""Contract checks for the identity-bound frozen DSMAPI implementation."""

import inspect
from typing import get_type_hints

from common.interfaces.dsm_api import DSMAPI
from dsm.runtime.runtime import DSMHandle


def test_handle_implements_frozen_dsmapi_method_signatures() -> None:
    """Handle methods match the frozen Protocol names, parameters, and types."""
    assert getattr(DSMAPI, "_is_protocol", False)
    assert DSMAPI in DSMHandle.__mro__

    for method_name in ("alloc", "read", "write", "free"):
        api_method = getattr(DSMAPI, method_name)
        handle_method = getattr(DSMHandle, method_name)
        api_signature = inspect.signature(api_method)
        handle_signature = inspect.signature(handle_method)

        assert list(api_signature.parameters) == list(handle_signature.parameters)
        assert [
            parameter.kind for parameter in api_signature.parameters.values()
        ] == [
            parameter.kind for parameter in handle_signature.parameters.values()
        ]
        assert get_type_hints(api_method) == get_type_hints(handle_method)
