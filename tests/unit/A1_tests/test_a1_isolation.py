import pytest

from memory_node.security.isolation import IsolationChecker
from common.types.errors import InvalidRequestError, NotOwnerError


def test_same_owner_allowed():
    assert IsolationChecker().check_access("alice", "alice") is None


def test_different_owner_rejected():
    with pytest.raises(NotOwnerError):
        IsolationChecker().check_access("alice", "bob")


def test_error_message_does_not_reveal_real_owner():
    with pytest.raises(NotOwnerError) as exc:
        IsolationChecker().check_access("alice-secret-id", "bob")
    assert "alice-secret-id" not in str(exc.value)


def test_empty_requester_rejected():
    with pytest.raises(InvalidRequestError):
        IsolationChecker().check_access("alice", "")