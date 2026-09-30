# Purpose:
# Creates a configured DefaultScheduler and selects the requested
# placement policy.

from common.types.scheduler import SchedulerConfig

from scheduler.policies.bandwidth import (
    BandwidthAwarePolicy,
)
from scheduler.policies.balanced import (
    BalancedPolicy,
)
from scheduler.policies.cpu import (
    CPUAwarePolicy,
)
from scheduler.policies.latency import (
    LatencyAwarePolicy,
)
from scheduler.policies.ram import (
    RAMAwarePolicy,
)
from scheduler.scheduler import DefaultScheduler


def create_scheduler(
    config: SchedulerConfig,
    node_state_provider=None,
    security_provider=None,
) -> DefaultScheduler:

    policies = {
        "ram": RAMAwarePolicy,
        "cpu": CPUAwarePolicy,
        "latency": LatencyAwarePolicy,
        "bandwidth": BandwidthAwarePolicy,
        "balanced": lambda: BalancedPolicy(config),
    }

    if config.policy not in policies:
        raise ValueError(
            f"Unknown scheduling policy: {config.policy}"
        )

    policy = policies[config.policy]()

    return DefaultScheduler(
        policy=policy,
        node_state_provider=node_state_provider,
        security_provider=security_provider,
    )
