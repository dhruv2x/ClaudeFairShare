"""Command-line interface.

Run as ``claude-fair-share <command>`` (console script) or
``python -m claude_fair_share <command>``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import __version__
from .config import (
    LONG_WINDOWS,
    STATUSLINE_CACHE_PATH,
    STATUSLINE_CACHE_TTL,
    WINDOW_LABELS,
    Config,
    load_config,
    load_state,
    parse_weekday,
    save_config,
    save_state,
)
from .report import Gauge, Report, build_report, render_status, render_statusline


# --------------------------------------------------------------------------- #
# statusline cache (so the badge doesn't rescan logs on every keystroke)
# --------------------------------------------------------------------------- #
def _write_statusline_cache(badge: str) -> None:
    try:
        STATUSLINE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUSLINE_CACHE_PATH.write_text(json.dumps({"badge": badge}))
    except OSError:
        pass


def _read_fresh_statusline_cache() -> str | None:
    try:
        age = dt.datetime.now().timestamp() - STATUSLINE_CACHE_PATH.stat().st_mtime
        if age < STATUSLINE_CACHE_TTL:
            return json.loads(STATUSLINE_CACHE_PATH.read_text())["badge"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


# --------------------------------------------------------------------------- #
# threshold notifications
# --------------------------------------------------------------------------- #
def _gauge_message(config: Config, gauge: Gauge, state: dict) -> str | None:
    """Check one gauge against its thresholds, updating ``state`` in place.

    Returns a reminder string iff a not-yet-notified threshold was crossed.
    ``state`` is keyed by window; each entry tracks its own period + notified
    thresholds, so a new week (or session) resets that gauge independently.
    """
    if gauge.pct is None:
        return None
    entry = state.get(gauge.window, {})
    same_period = entry.get("period_id") == gauge.period_id
    notified = list(entry.get("notified", [])) if same_period else []

    crossed = [t for t in config.thresholds if gauge.pct >= t and t not in notified]
    if not crossed:
        # Keep the period current so a later crossing compares correctly.
        state[gauge.window] = {"period_id": gauge.period_id, "notified": notified}
        return None

    highest = max(crossed)
    state[gauge.window] = {
        "period_id": gauge.period_id,
        "notified": sorted(set(notified) | set(crossed)),
    }
    icon = "🛑" if highest >= 100 else "⚠️"
    label = WINDOW_LABELS.get(gauge.window, gauge.window)
    return (
        f"{icon} LOCAL token budget: {gauge.pct:.0f}% used ({label}) on THIS "
        f"machine ({gauge.cost:.1f} / {gauge.cap:.1f} units). Crossed {highest}%. "
        f"The other machine's usage is NOT counted here."
    )


def _check_thresholds(config: Config, report: Report) -> str | None:
    """Return a combined reminder for every gauge that crossed, else ``None``."""
    state = load_state()
    messages = [m for m in (_gauge_message(config, g, state) for g in report.gauges) if m]
    save_state(state)
    return "\n".join(messages) if messages else None


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_status(_args) -> int:
    print(render_status(build_report(load_config())))
    return 0


def cmd_json(_args) -> int:
    print(json.dumps(build_report(load_config()).to_dict(), indent=2))
    return 0


def cmd_statusline(_args) -> int:
    cached = _read_fresh_statusline_cache()
    if cached is not None:
        sys.stdout.write(cached)
        return 0
    badge = render_statusline(build_report(load_config()))
    _write_statusline_cache(badge)
    sys.stdout.write(badge)
    return 0


def cmd_check(_args) -> int:
    config = load_config()
    report = build_report(config)
    _write_statusline_cache(render_statusline(report))  # keep badge warm
    message = _check_thresholds(config, report)
    if message:
        print(message)
    return 0


def cmd_set_plan(args) -> int:
    config = load_config()
    config.plan = args.units
    save_config(config)
    save_state({})  # cap meaning changed -> re-evaluate thresholds
    print(
        f"plan budget set: {config.plan:.1f} units per {config.window} "
        f"(={config.per_device_plan:.1f} on this machine of {config.device_count})"
    )
    return 0


def cmd_set_session(args) -> int:
    config = load_config()
    config.session = args.units
    save_config(config)
    save_state({})
    if config.session > 0:
        print(
            f"session budget set: {config.session:.1f} units per 5h "
            f"(={config.per_device_session:.1f} on this machine of {config.device_count})"
        )
    else:
        print("session gauge disabled")
    return 0


def cmd_set_devices(args) -> int:
    config = load_config()
    config.devices = args.n
    save_config(config)
    save_state({})  # per-device cap changed -> re-evaluate thresholds
    print(
        f"devices set: {config.device_count} "
        f"-> this machine = {config.per_device_plan:.1f} units/{config.window}"
        + (
            f", {config.per_device_session:.1f} units/5h"
            if config.per_device_session > 0
            else ""
        )
    )
    return 0


def cmd_set_window(args) -> int:
    config = load_config()
    config.window = args.window
    save_config(config)
    save_state({})  # window meaning changed -> reset notifications
    print(f"window set: {config.window}")
    return 0


def cmd_set_week_anchor(args) -> int:
    config = load_config()
    config.week_anchor = parse_weekday(args.day)
    save_config(config)
    save_state({})  # period meaning changed -> reset notifications
    name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][config.week_anchor]
    print(f"week resets on: {name}")
    return 0


def cmd_reset(_args) -> int:
    save_state({})
    print("threshold-notification state cleared")
    return 0


def cmd_install(_args) -> int:
    from .installer import install

    for action in install():
        print(f"  + {action}")
    print("done. Restart Claude Code (or open a new session) to see the badge.")
    return 0


def cmd_uninstall(_args) -> int:
    from .installer import uninstall

    for action in uninstall():
        print(f"  - {action}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-fair-share",
        description="Local, per-machine token usage tracker for Claude Code.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="human-readable usage report (default)")
    sub.add_parser("statusline", help="compact badge for the Claude Code statusline")
    sub.add_parser("json", help="machine-readable report")
    sub.add_parser("check", help="print a reminder only when a threshold is crossed")

    p_plan = sub.add_parser("set-plan", help="set the long-window budget (whole plan, in units)")
    p_plan.add_argument("units", type=float)

    p_session = sub.add_parser("set-session", help="set the 5h-session budget (units; 0 disables)")
    p_session.add_argument("units", type=float)

    p_devices = sub.add_parser("set-devices", help="number of machines to split budgets across")
    p_devices.add_argument("n", type=int)

    p_window = sub.add_parser("set-window", help="set the long tracking window")
    p_window.add_argument("window", choices=LONG_WINDOWS)

    p_anchor = sub.add_parser("set-week-anchor", help="weekday the week resets (mon..sun or 0..6)")
    p_anchor.add_argument("day")

    sub.add_parser("reset", help="clear notified-threshold state")
    sub.add_parser("install", help="wire the badge + reminders into settings.json")
    sub.add_parser("uninstall", help="remove the reminder hooks")
    return parser


_DISPATCH = {
    "status": cmd_status,
    "json": cmd_json,
    "statusline": cmd_statusline,
    "check": cmd_check,
    "set-plan": cmd_set_plan,
    "set-session": cmd_set_session,
    "set-devices": cmd_set_devices,
    "set-window": cmd_set_window,
    "set-week-anchor": cmd_set_week_anchor,
    "reset": cmd_reset,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "status"
    try:
        return _DISPATCH[command](args)
    except Exception:  # never let the statusline/hook path crash Claude Code
        if command in ("statusline", "check"):
            return 0
        raise


if __name__ == "__main__":
    raise SystemExit(main())
