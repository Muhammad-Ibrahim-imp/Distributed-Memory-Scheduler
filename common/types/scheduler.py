from dataclasses import dataclass

class SchedulerConfig:
    ram_weight: float
    cpu_weight: float
    latency_weight: float
    bandwidth_weight: float
    health_weight: float