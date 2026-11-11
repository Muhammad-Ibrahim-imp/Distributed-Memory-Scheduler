from common.types.errors import InvalidRequestError, NotOwnerError


class IsolationChecker:
    """Second line of defence: A2 binds the owner upstream, but the node checks again."""

    def check_access(self, object_owner: str, requesting_owner: str) -> None:
        if not isinstance(requesting_owner, str) or not requesting_owner:
            raise InvalidRequestError("owner_identity is missing")
        if object_owner != requesting_owner:
            # The message goes back over the wire, so it must not reveal the real owner.
            raise NotOwnerError("not the owner of this object")