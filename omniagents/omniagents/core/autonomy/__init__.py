"""Autonomy primitives: cron parsing, recurring loops, goal ticks, wakeups.

Domain-agnostic machinery for agents that act over time — recurring
maintenance loops, goal-driven autopilot ticks, and self-scheduled
wakeups. Products supply the prompts (via the ``set_*_prompt_builders``
seams); the framework supplies the clock, the state machines, and the
persistence/rehydration plumbing.
"""

from .arbitration import (
    active_tick_kinds,
    has_active_tick,
    register_active_tick,
    unregister_active_tick,
)
from .cron import (
    CronSchedule,
    interval_to_cron,
    next_cron_fire,
    parse_cron,
    parse_interval_seconds,
    split_loop_interval_and_prompt,
)
from .goal import GoalState, set_goal_prompt_builders
from .loop import (
    LoopTask,
    set_loop_default_prompt_resolver,
    set_loop_prompt_builders,
)
from .wakeup import WakeupSchedule

__all__ = [
    "CronSchedule",
    "GoalState",
    "LoopTask",
    "WakeupSchedule",
    "active_tick_kinds",
    "has_active_tick",
    "interval_to_cron",
    "next_cron_fire",
    "parse_cron",
    "parse_interval_seconds",
    "register_active_tick",
    "set_goal_prompt_builders",
    "set_loop_default_prompt_resolver",
    "set_loop_prompt_builders",
    "split_loop_interval_and_prompt",
    "unregister_active_tick",
]
