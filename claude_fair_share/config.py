"""Configuration, state, and filesystem paths.

Everything lives under the Claude config directory (``~/.claude`` by default,
or ``$CLAUDE_CONFIG_DIR`` if set), so the tracker is naturally scoped to the
same machine whose usage it reads.

Budgets are expressed in **usage units** — a model-weighted, cost-equivalent
proxy (see :mod:`claude_fair_share.pricing`). A unit is roughly one US dollar
of list-price API usage, but the point is the *weighting*: one Opus token costs
more units than one Haiku token, mirroring how the account quota actually drains.
Raw token counts are not used as the budget basis because they are not
comparable across models.

Two budgets are tracked at once:

* ``plan``    — the long window (default a calendar week).
* ``session`` — Anthropic's rolling 5-hour session window. ``0`` disables it.

Each is divided by ``devices`` to get *this* machine's fair share, so N machines
on one shared account each cap themselves at ``1/N`` of the plan.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
PROJECTS_DIR = CLAUDE_DIR / "projects"
TRACKER_DIR = CLAUDE_DIR / "token-tracker"
CONFIG_PATH = TRACKER_DIR / "config.json"
STATE_PATH = TRACKER_DIR / "state.json"
STATUSLINE_CACHE_PATH = TRACKER_DIR / "statusline-cache.json"

#: The dedicated session window; tracked alongside whichever long window is set.
SESSION_WINDOW = "5h"
#: Selectable long windows (the session window is always-on and not in here).
LONG_WINDOWS = ("week", "7d", "day", "month")
WINDOWS = LONG_WINDOWS + (SESSION_WINDOW,)

WINDOW_LABELS = {
    "week": "this week",
    "7d": "last 7 days",
    "day": "today",
    "month": "this month",
    "5h": "last 5h session",
}

#: Short statusline tags per window.
WINDOW_TAGS = {"week": "wk", "7d": "7d", "day": "d", "month": "mo", "5h": "5h"}

#: Weekday name -> Python weekday() index (Monday == 0).
WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

#: How long (seconds) a rendered statusline badge is reused before rescanning.
#: The statusline runs on every keystroke; without this cache that would mean
#: rescanning every log file on every render.
STATUSLINE_CACHE_TTL = 30


def parse_weekday(value: str) -> int:
    """Parse ``mon``..``sun`` (case-insensitive) or ``0``..``6`` to a weekday."""
    key = str(value).strip().lower()
    if key in WEEKDAYS:
        return WEEKDAYS[key]
    if key[:3] in WEEKDAYS:
        return WEEKDAYS[key[:3]]
    try:
        n = int(key)
    except ValueError:
        raise ValueError(f"not a weekday: {value!r} (use mon..sun or 0..6)")
    if not 0 <= n <= 6:
        raise ValueError(f"weekday out of range: {n} (0=Mon .. 6=Sun)")
    return n


@dataclass
class Config:
    """User-tunable settings. Budgets are in usage units (see module docstring)."""

    #: Long-window budget for the *whole* plan, before the per-device split.
    plan: float = 50.0
    #: Rolling 5h-session budget for the whole plan. ``0`` disables the gauge.
    session: float = 0.0
    #: Number of machines sharing the account; budgets are divided by this.
    devices: int = 1
    #: The long window: one of :data:`LONG_WINDOWS`.
    window: str = "week"
    #: Weekday the ``week`` window resets on (0=Mon..6=Sun), to match the day
    #: Anthropic renews your weekly quota.
    week_anchor: int = 0
    thresholds: list[int] = field(default_factory=lambda: [50, 80, 100])
    #: Optional per-model rate overrides; empty means "use built-in pricing".
    rates: dict = field(default_factory=dict)

    @property
    def device_count(self) -> int:
        return max(int(self.devices), 1)

    @property
    def per_device_plan(self) -> float:
        """This machine's share of the long-window budget."""
        return self.plan / self.device_count

    @property
    def per_device_session(self) -> float:
        """This machine's share of the 5h-session budget (``0`` when disabled)."""
        return self.session / self.device_count

    def to_dict(self) -> dict:
        data = {
            "plan": self.plan,
            "session": self.session,
            "devices": self.devices,
            "window": self.window,
            "week_anchor": self.week_anchor,
            "thresholds": self.thresholds,
        }
        if self.rates:
            data["rates"] = self.rates
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        # Accept the legacy ``budget_usd`` key as an alias for ``plan``.
        plan = data.get("plan", data.get("budget_usd", 50.0))
        return cls(
            plan=float(plan),
            session=float(data.get("session", 0.0)),
            devices=int(data.get("devices", 1)),
            window=str(data.get("window", "week")),
            week_anchor=int(data.get("week_anchor", 0)),
            thresholds=list(data.get("thresholds", [50, 80, 100])),
            rates=dict(data.get("rates", {})),
        )


_FIRST_RUN_TEMPLATE = {
    "plan": 50.0,
    "_plan_note": (
        "PLACEHOLDER weekly budget in usage units (~$ of list-price usage, "
        "model-weighted). This is the WHOLE plan; it is divided by `devices`. "
        "Set yours with `claude-fair-share set-plan <units>`."
    ),
    "session": 15.0,
    "_session_note": (
        "PLACEHOLDER 5h-session budget (whole plan, divided by `devices`). "
        "Set with `claude-fair-share set-session <units>`; 0 disables the session gauge."
    ),
    "devices": 1,
    "_devices_note": (
        "How many machines share this account. Set the SAME number on each so "
        "the budgets split evenly: `claude-fair-share set-devices <n>`."
    ),
    "window": "week",
    "_window_note": "long window: week | 7d | day | month",
    "week_anchor": 0,
    "_week_anchor_note": "weekday the week resets (0=Mon..6=Sun); match Anthropic's renewal day",
    "thresholds": [50, 80, 100],
}


def load_config() -> Config:
    """Load config, creating a documented default file on first run."""
    if not CONFIG_PATH.exists():
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(_FIRST_RUN_TEMPLATE, indent=2))
        return Config.from_dict(_FIRST_RUN_TEMPLATE)
    try:
        return Config.from_dict(json.loads(CONFIG_PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        # Never let a corrupt config crash the statusline; fall back to defaults.
        return Config()


def save_config(config: Config) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config.to_dict(), indent=2))


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))
