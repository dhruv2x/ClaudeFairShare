"""Model pricing and cost computation.

Base rates are USD per million tokens, expressed as ``(input, output)`` pairs.
Cache tokens are priced as multiples of the model's *input* rate, following
Anthropic's published multipliers:

==================  ==========================
Token category      Multiplier (x input rate)
==================  ==========================
cache read          0.10
cache write (5m)    1.25
cache write (1h)    2.00
==================  ==========================

These are list/API prices. For a Pro/Max subscription they are not what you are
billed — they are used here only as a *consistent proxy* for "how much have I
burned", which is the thing a shared-account user actually wants to cap.
"""

from __future__ import annotations

from dataclasses import dataclass

# USD per 1,000,000 tokens: (input, output)
BASE_RATES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "_default": (5.0, 25.0),
}

CACHE_READ_MULT = 0.10
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.00


@dataclass
class Usage:
    """Token counts from one or more assistant messages."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0

    @classmethod
    def from_message_usage(cls, usage: dict) -> "Usage":
        """Build a :class:`Usage` from a raw ``message.usage`` object.

        Cache-write tokens are split by TTL when the ``cache_creation``
        breakdown is present. When it is absent (older log format), the lump
        ``cache_creation_input_tokens`` is attributed to the 1-hour bucket,
        which is the default TTL Claude Code writes with.
        """
        cc = usage.get("cache_creation") or {}
        write_5m = int(cc.get("ephemeral_5m_input_tokens", 0) or 0)
        write_1h = int(cc.get("ephemeral_1h_input_tokens", 0) or 0)
        if not cc:
            write_1h += int(usage.get("cache_creation_input_tokens", 0) or 0)
        return cls(
            input=int(usage.get("input_tokens", 0) or 0),
            output=int(usage.get("output_tokens", 0) or 0),
            cache_read=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write_5m=write_5m,
            cache_write_1h=write_1h,
        )

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def total(self) -> int:
        return (
            self.input
            + self.output
            + self.cache_read
            + self.cache_write_5m
            + self.cache_write_1h
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write_5m=self.cache_write_5m + other.cache_write_5m,
            cache_write_1h=self.cache_write_1h + other.cache_write_1h,
        )


def rate_for(model: str | None, rates: dict | None = None) -> tuple[float, float]:
    """Resolve ``(input, output)`` rate for a model id.

    Tries an exact match, then a substring match (so dated snapshot ids resolve),
    then falls back to ``_default``.
    """
    rates = rates or BASE_RATES
    if not model:
        return rates["_default"]
    if model in rates:
        return rates[model]
    for key, value in rates.items():
        if key != "_default" and key in model:
            return value
    return rates["_default"]


def cost_usd(model: str | None, usage: Usage, rates: dict | None = None) -> float:
    """Cost-equivalent (USD) of ``usage`` for ``model`` at list prices."""
    rate_in, rate_out = rate_for(model, rates)
    micro = (
        usage.input * rate_in
        + usage.output * rate_out
        + usage.cache_read * CACHE_READ_MULT * rate_in
        + usage.cache_write_5m * CACHE_WRITE_5M_MULT * rate_in
        + usage.cache_write_1h * CACHE_WRITE_1H_MULT * rate_in
    )
    return micro / 1_000_000.0
