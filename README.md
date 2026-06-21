# 🪙 ClaudeFairShare

**Know how much you've spent on _this_ computer — before a shared Claude account runs dry.**

A tiny, zero-dependency CLI that reads Claude Code's own session logs and tells
you how many tokens (and how much cost-equivalent) you've burned **on this
machine** in a rolling window. It lives in your statusline and nudges you at
50% / 80% / 100% of a budget you choose.

```
Local Claude Code usage — THIS MACHINE only  (this machine = 1/3 of plan)
this week:
  ██████████░░░░░░░░░░ 51%   17.0 / 33.3 units
  tokens: in 38.7K | out 958.9K | cache-w 3.8M | cache-r 51.7M | total 56.6M
  by model:
    claude-sonnet-4-6            37.2M
    claude-opus-4-8              17.8M
    claude-haiku-4-5             1.6M
last 5h session:  78%   3.1 / 4.0 units
```

Statusline badge: `🪙 51% wk · 78% 5h` &nbsp;(green → amber → red as you climb,
colored by whichever window is hotter).

Budgets are in **usage units** — a model-weighted proxy where one Opus token
costs more than one Haiku token (≈ US dollars of list-price usage). You set the
budget for the **whole plan**; the tool divides it by your **device count** so N
machines on one account each cap at `1/N`. Two windows are tracked at once: a
long one (weekly) and Anthropic's rolling **5-hour session**.

---

## Why this exists

If you use **one Claude account on two computers** — say an office desktop and a
home laptop — the account's usage limit is shared, but neither machine shows you
*your* share. On a weekend when both are running, you can blow through the cap
without warning.

The catch: **Claude Code does not store your account-wide remaining quota
locally** — that lives on Anthropic's servers. So a truly "global" gauge is
impossible from your laptop.

But Claude Code **does** log every token it spends, per machine, in plain JSONL.
This tool reads those logs and counts **this machine's** usage against a budget
*you* set. Each computer has its own logs, so the split is automatic — **no IP
tracking, no network, no account access**. The filesystem _is_ the boundary.

---

## How it works

```
 ~/.claude/projects/<project>/<session>.jsonl   ← Claude Code writes these
        │   one JSON object per line; assistant
        │   messages carry message.usage + timestamp
        ▼
   ┌──────────┐   filter by time window   ┌──────────┐   model-weighted     ┌──────────┐
   │  scanner │ ────────────────────────▶ │  pricing │ ───────────────────▶ │  report  │
   └──────────┘   sum tokens per model     └──────────┘   tokens → units     └────┬─────┘
                                                                                  │
                       ┌──────────────────────────────┬──────────────────────────┤
                       ▼                               ▼                          ▼
                 statusline badge              50/80/100% reminder          status / json
                 (cached 30s)                  (SessionStart + Stop hooks)   (terminal / scripts)
```

**1. Read the logs.** Claude Code appends one JSON object per line to
`~/.claude/projects/**/*.jsonl`. Assistant messages include a `message.usage`
block (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation`) and a UTC `timestamp`. The scanner sums these, skipping files
whose modification time predates the window (a cheap prefilter) and tolerating
malformed lines.

**2. Window it — two at once.** Two gauges are tracked simultaneously: a **long**
window (`week` — the default; or `7d`, `day`, `month`) and Anthropic's rolling
**5-hour session** (`set-session`, account-wide and shared across your devices —
the thing that bites when one machine drains the session before the other gets a
turn). The `week` window resets on a weekday you choose (`set-week-anchor`) so it
can line up with the day Anthropic renews your weekly quota.

**3. Price it into usage units.** Tokens are converted to a **model-weighted
proxy** (using Anthropic's list prices) so the budget is comparable across
models — one Opus token costs more than one Haiku token, mirroring how the quota
actually drains. Raw token counts are deliberately *not* the basis: 1M Haiku ≠
1M Opus in quota terms. A "unit" is roughly one US dollar of list-price usage.

| Token type        | Price (× model input rate) |
| ----------------- | -------------------------- |
| input             | 1.00×                      |
| output            | model output rate          |
| cache **read**    | 0.10×                      |
| cache write (5m)  | 1.25×                      |
| cache write (1h)  | 2.00×                      |

> On a Pro/Max subscription a unit is **not your bill** — it's a stable yardstick
> so "50%" means the same thing every week. You don't know your plan's exact
> token quota (Anthropic doesn't publish it), so pick a whole-plan unit budget
> that reflects your observed usage and split it across devices.

**4. Split it per device.** The whole-plan budget is divided by `devices`, so
each of N machines caps itself at `1/N`. Set the **same** numbers on every
machine and each stays in its lane — no network, no peeking at the other box.

**5. Surface it.** A colored statusline badge (cached for 30s so it never
rescans on every keystroke), a one-line reminder per gauge when you cross a
threshold (fired from `SessionStart` and `Stop` hooks, once per threshold per
window), and `status` / `json` for the terminal or scripts.

---

## Install

Requires **Python 3.8+**. No third-party dependencies.

```bash
# from source (recommended while it's not on PyPI)
git clone https://github.com/dhruv2x/ClaudeFairShare
cd ClaudeFairShare
pipx install .          # or: pip install --user .
```

Then wire it into Claude Code (adds the statusline badge + reminder hooks,
backing up `settings.json` first):

```bash
claude-fair-share install
claude-fair-share set-plan 100       # whole-plan weekly budget, in usage units
claude-fair-share set-session 15     # whole-plan 5h-session budget (0 disables)
claude-fair-share set-devices 3      # split both budgets across 3 machines
claude-fair-share set-week-anchor thu  # week resets on the day Anthropic renews
```

> Set the **same** `set-plan`, `set-session`, and `set-devices` on every machine
> so the split is even — each then caps itself at `1/devices` of the plan.

Open a new Claude Code session and the `🪙` badge appears. No build step, no
daemon — the CLI runs on demand.

> Prefer not to install? Every command also works as
> `python3 -m claude_fair_share <command>` from a clone.

---

## Usage

```bash
claude-fair-share                    # status report (default)
claude-fair-share status             # same
claude-fair-share statusline         # the compact badge (used by the statusline)
claude-fair-share json               # machine-readable, for scripts/dashboards
claude-fair-share check              # print a reminder ONLY if a threshold was crossed
claude-fair-share set-plan 100       # whole-plan budget for the long window (units)
claude-fair-share set-session 15     # whole-plan 5h-session budget (units; 0 disables)
claude-fair-share set-devices 3      # machines to split both budgets across
claude-fair-share set-window week    # long window: week | 7d | day | month
claude-fair-share set-week-anchor thu # weekday the week resets (mon..sun or 0..6)
claude-fair-share reset              # clear "already notified" state for all windows
claude-fair-share install            # wire badge + hooks into settings.json (backs up)
claude-fair-share uninstall          # remove the hooks
```

---

## Configuration

`~/.claude/token-tracker/config.json`:

```json
{
  "plan": 100.0,
  "session": 15.0,
  "devices": 3,
  "window": "week",
  "week_anchor": 3,
  "thresholds": [50, 80, 100]
}
```

| Key           | Meaning                                                                      |
| ------------- | ---------------------------------------------------------------------------- |
| `plan`        | Whole-plan budget for the long window, in usage units (divided by `devices`).|
| `session`     | Whole-plan budget for the rolling 5h session; `0` disables that gauge.        |
| `devices`     | Machines sharing the account; both budgets are divided by this.              |
| `window`      | Long window: `week` · `7d` · `day` · `month`.                                |
| `week_anchor` | Weekday the `week` window resets on (`0`=Mon .. `6`=Sun).                    |
| `thresholds`  | Percentages that fire a reminder (each once per window, per gauge).          |
| `rates`       | *(optional)* override per-model `[input, output]` USD/1M-token prices.       |

> `budget_usd` from older configs is still read as an alias for `plan`.

The directory also holds `state.json` (which thresholds were already announced
this window) and `statusline-cache.json` (the 30s badge cache). Both are
disposable.

---

## What "local / per-machine" really means

- Each computer keeps its **own** `~/.claude/projects` logs, so summing them is
  inherently *this machine only*. That's the whole trick — no network needed.
- The tool **cannot** see your account-wide remaining quota (server-side) or the
  other machine's usage. It tracks *your local throughput against your budget*,
  which is exactly the "am I using more than my share this weekend?" signal.
- ⚠️ The **5h session** quota is account-wide and shared across machines. The
  gauge shows only *this* machine's contribution, so while two machines run at
  once it **understates** the true session draw. It still warns you when *your*
  share runs hot — which is what keeps you from starving the other machine.
- It **warns, never blocks.** Nothing here stops Claude Code; the split is by
  convention — same `set-devices` on each machine, each stays in its lane.
- ⚠️ **If you sync `~/.claude` across machines** (dotfiles repo, cloud drive),
  the logs mix and the per-machine split breaks. In that case point each machine
  at a separate dir via the `CLAUDE_CONFIG_DIR` environment variable, or don't
  sync the `projects/` folder.

---

## How the reminder fires

`claude-fair-share install` adds two hooks to `settings.json`:

```jsonc
"hooks": {
  "SessionStart": [{ "hooks": [{ "type": "command",
    "command": "<python> -m claude_fair_share check" }] }],
  "Stop":         [{ "hooks": [{ "type": "command",
    "command": "<python> -m claude_fair_share check" }] }]
}
```

`check` prints a line **only** when you cross a threshold you haven't been warned
about yet this window — so you get one ⚠️ at 50%, one at 80%, one 🛑 at 100%, and
silence otherwise. The statusline badge is refreshed on the same call.

The installer **merges** into existing settings and chains after any statusline
you already use (it detected and preserved a `[CAVEMAN]` badge on the author's
setup, for instance), and it always writes a `settings.json.bak` first.

---

## Uninstall

```bash
claude-fair-share uninstall              # removes the hooks
# to also drop the badge, restore the backup:
mv ~/.claude/settings.json.bak ~/.claude/settings.json
```

---

## Project layout

```
claude_fair_share/
├── pricing.py     # model rates + Usage dataclass + cost math
├── scanner.py     # read JSONL logs, window them, sum by model
├── report.py      # build the report + render (status / statusline / json)
├── config.py      # config, state, and ~/.claude paths
├── installer.py   # safe settings.json merge (install / uninstall)
├── cli.py         # argparse entry point + statusline cache + threshold logic
└── __main__.py    # python -m claude_fair_share
tests/test_tracker.py   # 17 unit tests, fixture-based (no real ~/.claude needed)
```

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

---

## FAQ

**Does this send my data anywhere?** No. It only reads local files and prints to
your terminal/statusline. Zero network calls, zero dependencies.

**Why units instead of tokens?** Raw tokens aren't comparable across models — 1M
Opus tokens drain far more quota than 1M Haiku. A unit weights tokens by model
price, so one budget is meaningful no matter which model you used.

**Is a unit my actual bill?** No — it's a list-price proxy (≈ USD) so a
percentage is meaningful and stable. Subscriptions don't bill per token.

**Why two gauges?** Anthropic limits you on *both* a weekly window and a rolling
5h session. You can blow the session cap mid-week, so the tool watches both and
colors the badge by whichever is hotter.

**Will the badge slow down my statusline?** No. The badge is cached for 30s and
served from a tiny file; logs are only rescanned when the cache is stale.

**It says 128% — is that a bug?** No, you're over the budget you set. Raise the
budget (`set-plan`) or, you know, take the hint. 🙂

---

## License

MIT © Dhruv Chauhan. See [LICENSE](LICENSE).
