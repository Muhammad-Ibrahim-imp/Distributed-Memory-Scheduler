"""Loads configs/security.yaml (policy-as-config). Only the Fernet key comes from the
environment (DSM_TOKEN_KEY), never from the file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


def _build(cls, raw: dict[str, Any] | None):
    raw = raw or {}
    unknown = set(raw) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    return cls(**raw)


@dataclass(frozen=True)
class TokenPolicy:
    ttl_seconds: int = 300


@dataclass(frozen=True)
class ReplayPolicy:                      # M5 defines, B1 enforces
    window_seconds: int = 60
    clock_skew_tolerance_seconds: int = 5


@dataclass(frozen=True)
class ConnectionRateLimit:               # B1
    bucket_capacity: int = 20
    refill_per_second: float = 5


@dataclass(frozen=True)
class NodeRateLimit:                     # A1
    max_memory_per_client: int | None = None
    max_objects_per_client: int | None = None


@dataclass(frozen=True)
class RateLimitPolicy:
    connection_level: ConnectionRateLimit = field(default_factory=ConnectionRateLimit)
    node_level: NodeRateLimit = field(default_factory=NodeRateLimit)


@dataclass(frozen=True)
class SuspiciousTrigger:
    failed_auths_per_window: int = 3
    window_seconds: int = 60


@dataclass(frozen=True)
class TrustPolicy:
    suspicious_trigger: SuspiciousTrigger = field(default_factory=SuspiciousTrigger)
    recovery_clean_period_seconds: int = 300


@dataclass(frozen=True)
class EnrollmentPolicy:
    """Names of env vars and the denylist file. Enforcement lands in Week 5."""
    node_secret_env: str = "DSM_NODE_ENROLLMENT_SECRET"
    client_secret_env: str = "DSM_CLIENT_ENROLLMENT_SECRET"
    denylist_path: str = "data/denylist.json"


@dataclass(frozen=True)
class AuditPolicy:
    path: str = "data/audit.jsonl"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5


@dataclass(frozen=True)
class SecurityConfig:
    token: TokenPolicy = field(default_factory=TokenPolicy)
    replay_protection: ReplayPolicy = field(default_factory=ReplayPolicy)
    rate_limit: RateLimitPolicy = field(default_factory=RateLimitPolicy)
    trust_state: TrustPolicy = field(default_factory=TrustPolicy)
    enrollment: EnrollmentPolicy = field(default_factory=EnrollmentPolicy)
    audit: AuditPolicy = field(default_factory=AuditPolicy)
    token_key: str | None = None         # from DSM_TOKEN_KEY; None => ephemeral dev key


def load_security_config(path: str | Path | None = None) -> SecurityConfig:
    raw: dict[str, Any] = {}
    if path is not None:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    unknown = set(raw) - {"token", "replay_protection", "rate_limit", "trust_state",
                          "enrollment", "audit"}
    if unknown:
        raise ValueError(f"unknown top-level security config keys: {sorted(unknown)}")
    rl = raw.get("rate_limit") or {}
    trust = dict(raw.get("trust_state") or {})
    trig = _build(SuspiciousTrigger, trust.pop("suspicious_trigger", None))
    _build(TrustPolicy, {**trust, "suspicious_trigger": None})      # rejects unknown keys
    return SecurityConfig(
        token=_build(TokenPolicy, raw.get("token")),
        replay_protection=_build(ReplayPolicy, raw.get("replay_protection")),
        rate_limit=RateLimitPolicy(
            connection_level=_build(ConnectionRateLimit, rl.get("connection_level")),
            node_level=_build(NodeRateLimit, rl.get("node_level"))),
        trust_state=TrustPolicy(suspicious_trigger=trig, **trust),
        enrollment=_build(EnrollmentPolicy, raw.get("enrollment")),
        audit=_build(AuditPolicy, raw.get("audit")),
        token_key=os.environ.get("DSM_TOKEN_KEY") or None,
    )