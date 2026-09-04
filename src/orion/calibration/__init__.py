"""Measuring what ORION's volatility thresholds were never derived from.

`orion.analysis.structure.classify_volatility` splits ATR% into LOW/MEDIUM/HIGH
at 0.3% and 0.8%, and has said in its own docstring since 2026-08-23 that those
are order-of-magnitude placeholders — not cut points taken from a historical
XAUUSD distribution, and not timeframe-aware although ATR% scales with the bar
interval. This package produces the distribution that gap refers to.

**IT MEASURES; IT NEVER CLASSIFIES.** Nothing here proposes, contains or hints
at a threshold, and `orion.analysis.structure` is neither imported nor
modified. Choosing cut points is a later, separate decision that must cite a
study rather than embed one — and a study that also recommended an answer would
make that citation circular.

The direction of dependency is one-way and enforced: this package reads
`orion.analysis`, `orion.data` and `orion.datasets`, and none of them may read
it. `orion.analysis` in particular sits below this one, so an import back would
be a cycle; a hygiene test asserts it does not exist.
"""
