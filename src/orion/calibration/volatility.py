"""The XAUUSD ATR% distribution study: measurement, and nothing else.

WHAT THIS EXISTS TO ANSWER. `orion.analysis.structure.classify_volatility` has
carried `low_atr_pct=0.003` / `high_atr_pct=0.008` since 2026-08-23, documented
in its own docstring and in `docs/DECISIONS.md` as order-of-magnitude
placeholders never derived from a historical XAUUSD distribution. Two gaps were
named there: no percentile analysis backs the cut points, and the same pair is
applied to M15, H1, H4 and D1 although ATR% scales with the bar interval. This
module produces the analysis. It does not close either gap — choosing
thresholds is a separate, later decision that must cite a study rather than
contain one.

**NOTHING HERE PROPOSES A THRESHOLD.** There is no LOW/MEDIUM/HIGH in this
module, no suggested cut point, and no field that could be mistaken for one.
`orion.analysis.structure` is not imported and is not modified.

**THE HELD-OUT SLICE IS NEVER SUMMARISED, STRUCTURALLY.** `calibration_end`
filters the samples before a single statistic is computed, so there is no code
path in this module that reads a bar at or after that boundary. A threshold
derived from this study therefore cannot have been influenced by the period it
will be validated against — not because a rule says so, but because the numbers
were never produced. The reverse direction is safe by construction too: Wilder
smoothing is strictly backward-looking, so a calibration sample's ATR can never
depend on a held-out bar.

**A PERCENTILE THE SAMPLE CANNOT SUPPORT IS OMITTED, NOT ESTIMATED.** A
quantile is only as stable as the number of observations beyond it, so each
requested percentile must clear `min_tail_observations` or it does not appear
in the result — the same refusal `orion.backtest.metrics` makes when a metric
would be meaningless, applied to a distribution instead of a ratio.

**REPRODUCIBILITY IS THE POINT.** `StudyInputs` carries everything the numbers
depend on, `study_fingerprint` is `orion.provenance.canonical_hash` over it,
and `STUDY_VERSION` pins the calculation definition. Identical inputs over an
identical dataset produce a byte-identical result; a change to how ATR% is
measured is a `STUDY_VERSION` bump, so two incomparable studies can never share
an identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from math import ceil

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.calibration.atr_series import AtrPercentSample, CalibrationError, atr_percent_series
from orion.data.models import DataSourceMeta, MarketSnapshot, Timeframe
from orion.data.sessions import TradingSession
from orion.data.validation import validate_snapshot
from orion.datasets.loader import LoadedDataset
from orion.provenance import canonical_hash

#: The calculation definition this module implements. Bump it whenever what a
#: number *means* changes — a different ATR variant, a different ATR% formula, a
#: different warm-up rule — so a study computed under the old definition can
#: never share an identity with one computed under the new. Follows
#: `orion.backtest.engine.ENGINE_VERSION` and
#: `orion.orchestrator.analysis.ORCHESTRATOR_VERSION`.
STUDY_VERSION = "2026-09-04.1"


class QuantileMethod(StrEnum):
    """How a percentile is interpolated between order statistics.

    ONE MEMBER, DELIBERATELY. NumPy's `percentile` accepts several methods and
    they give different answers, so leaving it implicit would make a study
    reproducible only on the machine that ran it. Only linear interpolation has
    been justified for this measurement, and a one-member enum says that
    honestly: a second method is not "disallowed by policy", it is
    unrepresentable until someone adds it deliberately — and adding it changes
    `study_fingerprint`, which is exactly the visibility that change deserves.
    The same reasoning `orion.connectors.schemas.Capability` records for its own
    single member.
    """

    LINEAR = "linear"


class PercentileValue(BaseModel):
    """One reported percentile of the ATR% distribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    percentile: float = Field(gt=0.0, lt=100.0)
    value: float


class StudyInputs(BaseModel):
    """Everything the reported numbers depend on — and nothing else.

    Hashed in full by `study_fingerprint`. Deliberately separate from the
    results it produces, mirroring `orion.datasets.manifest.DatasetIdentity`'s
    split from `DatasetManifest`: an identity that could absorb an output would
    change whenever the answer changed, which is precisely backwards.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    study_version: str = Field(min_length=1)
    dataset_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    symbol: str = Field(min_length=1)
    timeframe: Timeframe
    atr_length: int = Field(gt=0)
    warmup_bars: int = Field(gt=0)
    quantile_method: QuantileMethod
    min_tail_observations: int = Field(gt=0)
    calibration_end: datetime
    requested_percentiles: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_input_invariants(self) -> StudyInputs:
        if self.calibration_end.tzinfo is None:
            raise ValueError("calibration_end must be a timezone-aware datetime")
        if self.warmup_bars < self.atr_length:
            raise ValueError(
                f"warmup_bars ({self.warmup_bars}) must be at least atr_length ({self.atr_length})"
            )
        if any(not 0.0 < p < 100.0 for p in self.requested_percentiles):
            raise ValueError(
                f"every requested percentile must lie strictly inside (0, 100), got "
                f"{self.requested_percentiles}"
            )
        if len(set(self.requested_percentiles)) != len(self.requested_percentiles):
            raise ValueError(
                f"requested_percentiles contains duplicates: {self.requested_percentiles}"
            )
        if list(self.requested_percentiles) != sorted(self.requested_percentiles):
            raise ValueError(
                "requested_percentiles must be ascending so a fingerprint does not depend on "
                f"the order they were typed in, got {self.requested_percentiles}"
            )
        return self


class VolatilityDistribution(BaseModel):
    """One timeframe's measured ATR% distribution over a calibration slice.

    Carries no threshold, no classification and no recommendation — see this
    module's docstring. `study_fingerprint` is recomputed on construction, so a
    result edited by hand to claim an identity its inputs do not produce is
    rejected rather than believed, exactly as
    `orion.datasets.manifest.DatasetManifest` treats its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    inputs: StudyInputs
    study_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    # --- coverage: what the numbers below actually rest on ---
    first_sample_at: datetime
    last_sample_at: datetime
    sample_count: int = Field(gt=0)
    session_boundary_window_count: int = Field(ge=0)
    session_boundary_window_fraction: float = Field(ge=0.0, le=1.0)

    # --- distribution ---
    minimum: float
    maximum: float
    median: float
    percentiles: tuple[PercentileValue, ...]

    # --- secondary: ATR% is right-skewed, so these summarise less well than
    # the median does. Recorded because the gap between them is itself a
    # reading on how skewed a given timeframe is.
    mean: float
    stddev: float

    @model_validator(mode="after")
    def _check_result_invariants(self) -> VolatilityDistribution:
        expected = canonical_hash(self.inputs)
        if self.study_fingerprint != expected:
            raise ValueError(
                f"study_fingerprint {self.study_fingerprint!r} does not match these inputs "
                f"(which hash to {expected!r}); a study may not claim an identity it does "
                "not have"
            )
        if self.last_sample_at < self.first_sample_at:
            raise ValueError("last_sample_at is before first_sample_at")
        if self.maximum < self.minimum:
            raise ValueError("maximum is below minimum")
        if self.session_boundary_window_count > self.sample_count:
            raise ValueError(
                f"session_boundary_window_count ({self.session_boundary_window_count}) exceeds "
                f"sample_count ({self.sample_count})"
            )
        if list(self.percentiles) != sorted(self.percentiles, key=lambda p: p.percentile):
            raise ValueError("percentiles must be ascending")
        return self


def minimum_samples_for(percentile: float, *, min_tail_observations: int) -> int:
    """How many samples a percentile needs before it is worth reporting.

    A quantile estimate is only as stable as the number of observations in the
    tail beyond it, so the requirement is `min_tail_observations` in the smaller
    of the two tails: at 50 observations, p10 needs 500 samples, p5 needs 1,000
    and p1 needs 5,000. Stated as a function rather than a table so the rule is
    visible and a caller can tighten it by raising one number.
    """
    tail_fraction = min(percentile, 100.0 - percentile) / 100.0
    return ceil(min_tail_observations / tail_fraction)


def study_atr_percent(
    dataset: LoadedDataset,
    *,
    session: TradingSession,
    atr_length: int,
    warmup_bars: int,
    calibration_end: datetime,
    percentiles: Sequence[float],
    quantile_method: QuantileMethod,
    min_tail_observations: int,
) -> VolatilityDistribution:
    """Measure the ATR% distribution of `dataset`'s calibration slice.

    Takes a `LoadedDataset` rather than loose bars so the fingerprint the result
    reports cannot disagree with the bars it was computed from — `load_dataset`
    has already re-derived and verified that fingerprint against the file, and
    accepting the two separately would reintroduce exactly the mismatch that
    verification exists to prevent.

    The dataset is validated with `orion.data.validation.validate_snapshot`
    against the caller's declared `session` before anything is measured, and any
    finding aborts the study. That is what separates a market closure from a
    vendor hole: a weekend is expected and forgiven, while genuinely missing
    bars inject one enormous true range that Wilder smoothing then spreads
    across the following readings — silently distorting the distribution this
    study exists to report. `now` is the dataset's own last bar, so staleness
    cannot fire on history, following `orion.backtest.engine.run_backtest`'s
    precedent for validating a historical range.

    Every parameter is required and recorded. Raises `CalibrationError` if the
    data fails validation, if the calibration slice is empty or too small for a
    dispersion statistic, or if no requested percentile is supportable —
    never a result with a fabricated field.

    Pure: no clock, no network, no filesystem, no randomness.
    """
    identity = dataset.manifest.identity
    _validate_or_raise(dataset, session=session)

    samples = atr_percent_series(
        dataset.bars,
        timeframe=identity.timeframe,
        session=session,
        atr_length=atr_length,
        warmup_bars=warmup_bars,
    )
    calibration = tuple(s for s in samples if s.as_of < calibration_end)
    if not calibration:
        raise CalibrationError(
            f"no sample falls before calibration_end ({calibration_end.isoformat()}); the "
            f"{len(samples)} available samples start at "
            f"{samples[0].as_of.isoformat() if samples else 'n/a'}"
        )
    if len(calibration) < 2:
        raise CalibrationError(
            f"a dispersion statistic is undefined for {len(calibration)} sample(s); at least "
            "two are needed for a standard deviation"
        )

    inputs = StudyInputs(
        study_version=STUDY_VERSION,
        dataset_fingerprint=dataset.manifest.fingerprint,
        symbol=identity.symbol,
        timeframe=identity.timeframe,
        atr_length=atr_length,
        warmup_bars=warmup_bars,
        quantile_method=quantile_method,
        min_tail_observations=min_tail_observations,
        calibration_end=calibration_end,
        requested_percentiles=tuple(percentiles),
    )
    return _summarise(calibration, inputs=inputs)


def _validate_or_raise(dataset: LoadedDataset, *, session: TradingSession) -> None:
    identity = dataset.manifest.identity
    snapshot = MarketSnapshot(
        source=DataSourceMeta(
            provider=dataset.manifest.provider,
            symbol=identity.symbol,
            timeframe=identity.timeframe,
            # The dataset's own last bar: `validate_snapshot` measures staleness
            # against `now`, and historical data is stale by definition. Only
            # EMPTY/GAP/NON_MONOTONIC/DUPLICATE can fire here, which is exactly
            # what this call is for.
            retrieved_at=identity.last_bar_timestamp,
        ),
        bars=list(dataset.bars),
    )
    validation = validate_snapshot(snapshot, now=identity.last_bar_timestamp, session=session)
    if not validation.is_valid:
        findings = "; ".join(f"{f.issue.value}: {f.detail}" for f in validation.findings)
        raise CalibrationError(
            f"dataset {dataset.manifest.fingerprint[:16]}... "
            f"({identity.symbol}/{identity.timeframe.value}) is not fit to measure: {findings}"
        )


def _summarise(
    calibration: tuple[AtrPercentSample, ...], *, inputs: StudyInputs
) -> VolatilityDistribution:
    values = np.array([s.atr_percent for s in calibration], dtype=np.float64)
    sample_count = len(calibration)

    supported = [
        p
        for p in inputs.requested_percentiles
        if sample_count
        >= minimum_samples_for(p, min_tail_observations=inputs.min_tail_observations)
    ]
    if not supported:
        raise CalibrationError(
            f"{sample_count} samples support none of the requested percentiles "
            f"{inputs.requested_percentiles} at {inputs.min_tail_observations} tail "
            "observations; a distribution with no reportable percentile is not a result"
        )

    quantiles = np.percentile(values, supported, method=inputs.quantile_method.value)
    boundary_count = sum(1 for s in calibration if s.crosses_session_boundary)

    return VolatilityDistribution(
        inputs=inputs,
        study_fingerprint=canonical_hash(inputs),
        first_sample_at=calibration[0].as_of,
        last_sample_at=calibration[-1].as_of,
        sample_count=sample_count,
        session_boundary_window_count=boundary_count,
        session_boundary_window_fraction=boundary_count / sample_count,
        minimum=float(values.min()),
        maximum=float(values.max()),
        median=float(np.percentile(values, 50.0, method=inputs.quantile_method.value)),
        percentiles=tuple(
            PercentileValue(percentile=p, value=float(v))
            for p, v in zip(supported, np.atleast_1d(quantiles), strict=True)
        ),
        mean=float(values.mean()),
        stddev=float(values.std(ddof=1)),
    )
