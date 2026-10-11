"""Build the central-security core in one call (used by the single coordinator process)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.types.security import TrustStateReader
from security.authentication.registration import IdentityStore
from security.authentication.service import AuthService
from security.authentication.tokens import TokenService
from security.config import SecurityConfig, load_security_config
from security.monitoring import audit as audit_module
from security.monitoring.audit import AuditLogger
from security.sessions.manager import SessionManager


@dataclass
class SecurityCore:
    config: SecurityConfig
    identities: IdentityStore
    sessions: SessionManager
    tokens: TokenService
    audit: AuditLogger
    auth: AuthService


def build_security_core(config: SecurityConfig | None = None, config_path: str | Path | None = None,
                        clock: Callable[[], float] = time.time,
                        trust: TrustStateReader | None = None,
                        set_default_audit: bool = False) -> SecurityCore:
    cfg = config or load_security_config(config_path)
    audit = AuditLogger(cfg.audit.path, cfg.audit.max_bytes, cfg.audit.backup_count, clock)
    if set_default_audit:
        audit_module.configure(audit)       # lets A1/B1 call audit.log_security_event(...)
    sessions = SessionManager(cfg.token.ttl_seconds, clock=clock, audit=audit)
    tokens = TokenService(cfg.token_key, cfg.token.ttl_seconds, clock)
    identities = IdentityStore(clock)
    auth = AuthService(identities, sessions, tokens, audit, trust)
    return SecurityCore(cfg, identities, sessions, tokens, audit, auth)