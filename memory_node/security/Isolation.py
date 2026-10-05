from common.types.errors import NotOwnerError

class IsolationChecker:
    """Second line of defense — A2 doesn't fully auth-check owner,
    so this must actually run. Raises NotOwnerError (shared class)."""

    def check_access(self, object_owner: str, requesting_owner: str) -> None:
        raise NotImplementedError
        # once implemented:
        # if object_owner != requesting_owner:
        #     raise NotOwnerError(f"object owned by {object_owner}")