"""Local, per-machine token usage tracker for Claude Code.

Reads Claude Code's own session logs (``~/.claude/projects/**/*.jsonl``) to
report how many tokens — and how much cost-equivalent — you've spent *on this
machine* in a rolling window, and warns when you cross a budget threshold.

See the README for the full design rationale.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
