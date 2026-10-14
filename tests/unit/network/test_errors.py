from common.types.errors import InvalidRequestError, ObjectNotFoundError, QuotaExceededError
from network.tcp.errors import code_to_error, error_to_code


def test_roundtrip():
    for exc in (ObjectNotFoundError("x"), QuotaExceededError("x"), InvalidRequestError("x")):
        assert type(code_to_error(error_to_code(exc), str(exc))) is type(exc)


def test_unknown_falls_back():
    assert isinstance(code_to_error("NOPE", "m"), InvalidRequestError)
