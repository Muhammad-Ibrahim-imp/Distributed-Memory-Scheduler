from enum import Enum

class SecurityState(Enum):
    TRUSTED = "trusted",
    SUSPICIOUS = "suspicious",
    REVOKED = "revoked",
    QUARANTINED = "quarantined",
