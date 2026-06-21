"""Unit tests — no real ~/.claude needed; everything runs against fixtures."""

import datetime as dt
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_fair_share.config import Config
from claude_fair_share.pricing import (
    CACHE_READ_MULT,
    Usage,
    cost_usd,
    rate_for,
)
from claude_fair_share.report import build_report, humanize_tokens
from claude_fair_share.scanner import scan, window_bounds


def _log_line(model, ts, **usage):
    return json.dumps({"timestamp": ts, "message": {"model": model, "usage": usage}})


class PricingTests(unittest.TestCase):
    def test_rate_resolution(self):
        self.assertEqual(rate_for("claude-opus-4-8"), (5.0, 25.0))
        self.assertEqual(rate_for("claude-haiku-4-5-20251001"), (1.0, 5.0))  # substring
        self.assertEqual(rate_for("unknown-model"), (5.0, 25.0))  # default
        self.assertEqual(rate_for(None), (5.0, 25.0))

    def test_usage_from_message(self):
        u = Usage.from_message_usage(
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 2,
                    "ephemeral_1h_input_tokens": 8,
                },
            }
        )
        self.assertEqual(u.input, 100)
        self.assertEqual(u.cache_write, 10)
        self.assertEqual(u.total, 100 + 50 + 10 + 2 + 8)

    def test_missing_cache_breakdown_falls_back_to_1h(self):
        u = Usage.from_message_usage(
            {"input_tokens": 1, "cache_creation_input_tokens": 40}
        )
        self.assertEqual(u.cache_write_1h, 40)
        self.assertEqual(u.cache_write_5m, 0)

    def test_cost_math(self):
        # 1M input on opus (=$5) + 1M cache read (=0.1x => $0.50)
        u = Usage(input=1_000_000, cache_read=1_000_000)
        expected = 5.0 + 1_000_000 * CACHE_READ_MULT * 5.0 / 1_000_000
        self.assertAlmostEqual(cost_usd("claude-opus-4-8", u), expected)


class WindowTests(unittest.TestCase):
    def test_week_starts_monday_by_default(self):
        wed = dt.datetime(2026, 6, 17, 14, 0, tzinfo=dt.timezone.utc)  # a Wednesday
        start, pid = window_bounds("week", wed)
        self.assertEqual(start.weekday(), 0)  # Monday
        self.assertEqual((start.hour, start.minute), (0, 0))
        self.assertEqual(pid, "wk-2026-06-15")  # the preceding Monday

    def test_week_anchor_shifts_reset_day(self):
        wed = dt.datetime(2026, 6, 17, 14, 0, tzinfo=dt.timezone.utc)  # a Wednesday
        # Anchor on Thursday(=3): the reset day before Wed-17 is Thu-11.
        start, pid = window_bounds("week", wed, week_anchor=3)
        self.assertEqual(start.weekday(), 3)  # Thursday
        self.assertEqual(start.date(), dt.date(2026, 6, 11))
        self.assertEqual(pid, "wk-2026-06-11")

    def test_week_anchor_on_the_anchor_day_starts_today(self):
        wed = dt.datetime(2026, 6, 17, 14, 0, tzinfo=dt.timezone.utc)
        start, _ = window_bounds("week", wed, week_anchor=2)  # Wed == today
        self.assertEqual(start.date(), wed.date())

    def test_rolling_5h(self):
        now = dt.datetime(2026, 6, 17, 14, 0, tzinfo=dt.timezone.utc)
        start, _ = window_bounds("5h", now)
        self.assertEqual(now - start, dt.timedelta(hours=5))


class ScanAndReportTests(unittest.TestCase):
    def _projects(self, tmp, lines):
        d = Path(tmp) / "projects" / "-some-proj"
        d.mkdir(parents=True)
        (d / "session.jsonl").write_text("\n".join(lines))
        return Path(tmp) / "projects"

    def test_scan_filters_by_window_and_sums_by_model(self):
        now = dt.datetime.now(dt.timezone.utc)
        inside = now.isoformat()
        outside = (now - dt.timedelta(days=30)).isoformat()
        with TemporaryDirectory() as tmp:
            projects = self._projects(
                tmp,
                [
                    _log_line("claude-opus-4-8", inside, input_tokens=100, output_tokens=10),
                    _log_line("claude-opus-4-8", inside, input_tokens=50),
                    _log_line("claude-haiku-4-5", inside, output_tokens=200),
                    _log_line("claude-opus-4-8", outside, input_tokens=9999),  # excluded
                    "not json at all",  # malformed line is skipped
                ],
            )
            totals = scan(projects, now - dt.timedelta(days=7))
        self.assertEqual(totals["claude-opus-4-8"].input, 150)
        self.assertEqual(totals["claude-opus-4-8"].output, 10)
        self.assertEqual(totals["claude-haiku-4-5"].output, 200)

    def test_build_report_percent(self):
        now = dt.datetime.now(dt.timezone.utc)
        with TemporaryDirectory() as tmp:
            projects = self._projects(
                tmp,
                [_log_line("claude-opus-4-8", now.isoformat(), input_tokens=2_000_000)],
            )
            # 2M opus input = 10 units; plan 20 -> 50%
            report = build_report(
                Config(plan=20.0, window="7d"), now=now, projects_dir=projects
            )
        self.assertAlmostEqual(report.long.cost, 10.0)
        self.assertAlmostEqual(report.long.pct, 50.0)
        self.assertEqual(report.long.totals.input, 2_000_000)
        self.assertIsNone(report.session)  # session off by default (session=0)

    def test_device_split_halves_the_cap(self):
        now = dt.datetime.now(dt.timezone.utc)
        with TemporaryDirectory() as tmp:
            projects = self._projects(
                tmp,
                [_log_line("claude-opus-4-8", now.isoformat(), input_tokens=2_000_000)],
            )
            # Same 10 units of usage, but plan 20 split across 2 machines -> cap 10
            report = build_report(
                Config(plan=20.0, devices=2, window="7d"),
                now=now,
                projects_dir=projects,
            )
        self.assertAlmostEqual(report.long.cap, 10.0)
        self.assertAlmostEqual(report.long.pct, 100.0)

    def test_session_gauge_tracked_when_enabled(self):
        now = dt.datetime.now(dt.timezone.utc)
        with TemporaryDirectory() as tmp:
            projects = self._projects(
                tmp,
                [_log_line("claude-opus-4-8", now.isoformat(), input_tokens=1_000_000)],
            )
            report = build_report(
                Config(plan=50.0, session=10.0, window="week"),
                now=now,
                projects_dir=projects,
            )
        self.assertIsNotNone(report.session)
        self.assertEqual(report.session.window, "5h")
        self.assertAlmostEqual(report.session.cost, 5.0)  # 1M opus input
        self.assertAlmostEqual(report.session.pct, 50.0)  # 5 of 10 units

    def test_no_budget_means_no_percent(self):
        now = dt.datetime.now(dt.timezone.utc)
        with TemporaryDirectory() as tmp:
            projects = self._projects(tmp, [])
            report = build_report(Config(plan=0.0), now=now, projects_dir=projects)
        self.assertIsNone(report.long.pct)


class NotifyTests(unittest.TestCase):
    def _gauge(self, window, pct, period_id="p1"):
        from claude_fair_share.report import Gauge

        return Gauge(
            window=window,
            period_id=period_id,
            start=dt.datetime.now(dt.timezone.utc),
            cost=pct,
            cap=100.0,
            by_model={},
            totals=Usage(),
            pct=pct,
        )

    def test_fires_once_per_threshold_then_stays_quiet(self):
        from claude_fair_share.cli import _gauge_message

        cfg = Config(thresholds=[50, 80, 100])
        state: dict = {}
        # 60% crosses 50 only.
        msg = _gauge_message(cfg, self._gauge("week", 60.0), state)
        self.assertIn("50%", msg)
        self.assertEqual(state["week"]["notified"], [50])
        # Still 60% next check -> already notified -> silent.
        self.assertIsNone(_gauge_message(cfg, self._gauge("week", 60.0), state))

    def test_new_period_resets_notifications(self):
        from claude_fair_share.cli import _gauge_message

        cfg = Config(thresholds=[50])
        state: dict = {}
        _gauge_message(cfg, self._gauge("week", 60.0, period_id="p1"), state)
        # A new week id -> same 60% should notify again.
        msg = _gauge_message(cfg, self._gauge("week", 60.0, period_id="p2"), state)
        self.assertIsNotNone(msg)

    def test_gauges_are_tracked_independently(self):
        from claude_fair_share.cli import _gauge_message

        cfg = Config(thresholds=[50])
        state: dict = {}
        _gauge_message(cfg, self._gauge("week", 60.0), state)  # week notified
        # 5h gauge crossing is independent of the week gauge's state.
        msg = _gauge_message(cfg, self._gauge("5h", 70.0), state)
        self.assertIsNotNone(msg)
        self.assertIn("week", state)
        self.assertIn("5h", state)


class FormatTests(unittest.TestCase):
    def test_humanize(self):
        self.assertEqual(humanize_tokens(999), "999")
        self.assertEqual(humanize_tokens(1500), "1.5K")
        self.assertEqual(humanize_tokens(2_300_000), "2.3M")


if __name__ == "__main__":
    unittest.main()
