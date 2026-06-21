"""Reads Claude Code session logs and aggregates token usage by model.

Claude Code appends one JSON object per line to
``~/.claude/projects/<slug>/<session>.jsonl``. Assistant messages carry a
``message.usage`` object with the token counts and a ``timestamp`` (UTC ISO
8601). We sum those, filtered to a time window. Because each machine keeps its
own ``projects`` directory, the sum is inherently *this machine only*.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .pricing import Usage


def window_bounds(
    window: str, now: dt.datetime, week_anchor: int = 0
) -> tuple[dt.datetime, str]:
    """Return ``(start, period_id)`` for a window anchored at ``now`` (aware).

    ``period_id`` is a stable label for the current window instance; threshold
    notifications reset whenever it changes (e.g. a new calendar week).

    ``week_anchor`` (0=Mon..6=Sun) sets the weekday the ``week`` window resets
    on, so it can be lined up with the day Anthropic renews the weekly quota.
    """
    if window == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start.strftime("%Y-%m-%d")
    if window == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, start.strftime("%Y-%m")
    if window == "7d":
        return now - dt.timedelta(days=7), now.strftime("%Y-%m-%d")
    if window == "5h":
        start = now - dt.timedelta(hours=5)
        return start, now.strftime("%Y-%m-%d-%H")
    # default: rolling calendar week, since the anchor weekday at 00:00 local.
    days_since_anchor = (now.weekday() - week_anchor) % 7
    start = (now - dt.timedelta(days=days_since_anchor)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Anchor-safe id: the start date itself (ISO week numbering assumes Monday).
    return start, "wk-" + start.strftime("%Y-%m-%d")


def parse_timestamp(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _iter_log_files(projects_dir: Path, since: dt.datetime):
    """Yield log files that *could* contain entries newer than ``since``.

    Skips files whose mtime predates the window — a cheap prefilter that avoids
    opening months of stale logs on every statusline render.
    """
    if not projects_dir.exists():
        return
    for path in projects_dir.glob("**/*.jsonl"):
        try:
            mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        except OSError:
            continue
        if mtime >= since:
            yield path


def scan(projects_dir: Path, since: dt.datetime) -> dict[str, Usage]:
    """Aggregate usage by model id for messages with ``timestamp >= since``."""
    totals: dict[str, Usage] = {}
    for path in _iter_log_files(projects_dir, since):
        try:
            with path.open(encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if '"usage"' not in line:  # cheap reject before json.loads
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = record.get("message") or {}
                    raw_usage = message.get("usage")
                    if not raw_usage:
                        continue
                    stamp = parse_timestamp(record.get("timestamp", ""))
                    if stamp is None or stamp < since:
                        continue
                    model = message.get("model") or "_default"
                    usage = Usage.from_message_usage(raw_usage)
                    totals[model] = totals.get(model, Usage()) + usage
        except OSError:
            continue
    return totals
