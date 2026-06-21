"""Builds usage reports and renders them for humans, the statusline, and JSON.

A :class:`Report` holds one :class:`Gauge` per tracked window: a ``long`` gauge
(the configured week/7d/day/month window) and, when enabled, a ``session`` gauge
for Anthropic's rolling 5h window. Each gauge compares *this machine's* usage
against *this machine's share* of the budget (the whole-plan budget divided by
the device count).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROJECTS_DIR, SESSION_WINDOW, WINDOW_LABELS, WINDOW_TAGS, Config
from .pricing import Usage, cost_usd
from .scanner import scan, window_bounds


@dataclass
class Gauge:
    """One window's usage measured against this machine's share of the budget."""

    window: str
    period_id: str
    start: dt.datetime
    cost: float  #: usage units consumed by this machine in this window
    cap: float  #: this machine's share of the budget (0 => no cap set)
    by_model: dict[str, Usage]
    totals: Usage
    #: ``None`` when no cap is set, else percent of the per-device cap consumed.
    pct: float | None = field(default=None)

    def to_dict(self) -> dict:
        return {
            "window": self.window,
            "period_id": self.period_id,
            "start": self.start.isoformat(),
            "cost_units": round(self.cost, 4),
            "cap_units": round(self.cap, 4),
            "pct": (round(self.pct, 1) if self.pct is not None else None),
            "total_tokens": self.totals.total,
            "tokens": {
                "input": self.totals.input,
                "output": self.totals.output,
                "cache_read": self.totals.cache_read,
                "cache_write": self.totals.cache_write,
            },
            "by_model": {m: u.total for m, u in self.by_model.items()},
        }


def _build_gauge(
    window: str, cap: float, config: Config, now: dt.datetime, projects_dir: Path
) -> Gauge:
    start, period_id = window_bounds(window, now, config.week_anchor)
    by_model = scan(projects_dir, start)
    rates = config.rates or None

    totals = Usage()
    cost = 0.0
    for model, usage in by_model.items():
        totals = totals + usage
        cost += cost_usd(model, usage, rates)

    pct = (cost / cap * 100.0) if cap > 0 else None
    return Gauge(
        window=window,
        period_id=period_id,
        start=start,
        cost=cost,
        cap=cap,
        by_model=by_model,
        totals=totals,
        pct=pct,
    )


@dataclass
class Report:
    """The full picture: a long-window gauge plus an optional session gauge."""

    devices: int
    long: Gauge
    session: Gauge | None = None

    @property
    def gauges(self) -> list[Gauge]:
        """Active gauges, long first."""
        return [self.long] + ([self.session] if self.session else [])

    @property
    def hottest(self) -> Gauge:
        """The gauge closest to (or furthest past) its cap — drives badge color."""
        return max(self.gauges, key=lambda g: g.pct if g.pct is not None else -1.0)

    def to_dict(self) -> dict:
        return {
            "devices": self.devices,
            "long": self.long.to_dict(),
            "session": self.session.to_dict() if self.session else None,
        }


def build_report(
    config: Config,
    now: dt.datetime | None = None,
    projects_dir: Path | None = None,
) -> Report:
    now = now or dt.datetime.now().astimezone()
    projects_dir = projects_dir or PROJECTS_DIR

    long = _build_gauge(
        config.window, config.per_device_plan, config, now, projects_dir
    )
    session = None
    if config.per_device_session > 0:
        session = _build_gauge(
            SESSION_WINDOW, config.per_device_session, config, now, projects_dir
        )
    return Report(devices=config.device_count, long=long, session=session)


def humanize_tokens(n: int) -> str:
    for suffix, threshold in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= threshold:
            return f"{n / threshold:.1f}{suffix}"
    return str(int(n))


def _badge_color(pct: float) -> int:
    if pct < 50:
        return 70  # green
    if pct < 80:
        return 178  # amber
    if pct < 100:
        return 203  # red
    return 196  # bright red


def _share_note(report: Report) -> str:
    return f"  (this machine = 1/{report.devices} of plan)" if report.devices > 1 else ""


def render_status(report: Report) -> str:
    """Multi-line human report for the terminal."""
    g = report.long
    label = WINDOW_LABELS.get(g.window, g.window)
    lines = [f"Local Claude Code usage — THIS MACHINE only{_share_note(report)}"]

    # Long-window gauge: bar + breakdown.
    lines.append(f"{label}:")
    if g.pct is not None:
        filled = min(int(g.pct / 5), 20)
        bar = "█" * filled + "░" * (20 - filled)
        lines.append(f"  {bar} {g.pct:.0f}%   {g.cost:.1f} / {g.cap:.1f} units")
    else:
        lines.append(f"  {g.cost:.1f} units  (no budget set)")

    t = g.totals
    lines.append(
        f"  tokens: in {humanize_tokens(t.input)} | out {humanize_tokens(t.output)} "
        f"| cache-w {humanize_tokens(t.cache_write)} "
        f"| cache-r {humanize_tokens(t.cache_read)} "
        f"| total {humanize_tokens(t.total)}"
    )

    models = [(m, u) for m, u in g.by_model.items() if u.total > 0]
    if models:
        lines.append("  by model:")
        for model, usage in sorted(models, key=lambda kv: -kv[1].total):
            lines.append(f"    {model:<28} {humanize_tokens(usage.total)}")

    # Session gauge: one summary line.
    if report.session is not None:
        s = report.session
        slabel = WINDOW_LABELS.get(s.window, s.window)
        if s.pct is not None:
            lines.append(f"{slabel}:  {s.pct:.0f}%   {s.cost:.1f} / {s.cap:.1f} units")
        else:
            lines.append(f"{slabel}:  {s.cost:.1f} units")
    return "\n".join(lines)


def _badge_segment(gauge: Gauge) -> str:
    tag = WINDOW_TAGS.get(gauge.window, gauge.window)
    return f"{gauge.pct:.0f}% {tag}"


def render_statusline(report: Report) -> str:
    """Compact, ANSI-colored badge for the Claude Code statusline.

    Shows every capped gauge (``51% wk · 78% 5h``), colored by the hottest one.
    """
    capped = [g for g in report.gauges if g.pct is not None]
    if not capped:
        return f"\033[38;5;245m🪙 {report.long.cost:.1f}u\033[0m"
    color = _badge_color(report.hottest.pct)
    body = " · ".join(_badge_segment(g) for g in capped)
    return f"\033[38;5;{color}m🪙 {body}\033[0m"
