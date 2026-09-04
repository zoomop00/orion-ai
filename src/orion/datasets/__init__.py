"""Reproducible historical datasets: acquire once, identify by content, replay
from a file.

A dataset is a CSV of real vendor bars plus a small committed manifest naming
what those bars are. The CSV stays out of version control under `/data/`; the
manifest goes into it. Between them, a backtest result can cite the exact
history it was produced from, and anyone can re-acquire that history and prove
they hold the same thing.

This package never imports `orion.backtest`, `orion.analysis` or any strategy
code — a hygiene test asserts it. Acquisition and replay meet at a file on
disk, never in a process, which is what keeps `orion.backtest` free of vendors
and credentials and what makes a replay reproducible rather than dependent on
whatever a live endpoint answers today.
"""
