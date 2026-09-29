# Purpose:
# Defines the security/trust states consumed by B2's scheduler.
# M5 owns the state machine; B2 only consumes these states.

from enum import Enum


class SecurityState(Enum):
    TRUSTED = "trusted"
    SUSPICIOUS = "suspicious"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"
