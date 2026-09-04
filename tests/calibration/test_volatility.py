"""The study reports a distribution, never a threshold — and never reads the
held-out slice.

The load-bearing tests: the held-out period cannot influence the result, an
unsupportable percentile is omitted rather than estimated, identical inputs
produce byte-identical output, and a genuine data gap aborts the measurement
while a declared weekly closure does not.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from orion.calibration.atr_series import CONVERGED_WARMUP_BARS, CalibrationError
from orion.calibration.volatility import (
    STUDY_VERSION,
    QuantileMethod,
    StudyInputs,
    VolatilityDistribution,
    minimum_samples_for,
    study_atr_percent,
)
from orion.data.models import Timeframe
from orion.data.sessions import XAUUSD_WEEKLY
from orion.provenance import canonical_hash
from tests.calibration.conftest import (
    build_loaded_dataset,
    make_walk_bars,
    make_weekend_bars,
)

ATR_LENGTH = 14
WARMUP = 20
PERCENTILES = (5.0, 10.0, 25.0, 75.0, 90.0, 95.0)


def _study(bars: list, *, calibration_end=None, percentiles=PERCENTILES, warmup=WARMUP, **kw):
    dataset = build_loaded_dataset(bars)
    return study_atr_percent(
        dataset,
        session=XAUUSD_WEEKLY,
        atr_length=ATR_LENGTH,
        warmup_bars=warmup,
        calibration_end=(
            calibration_end if calibration_end is not None else bars[-1].timestamp + timedelta(1)
        ),
        percentiles=percentiles,
        quantile_method=QuantileMethod.LINEAR,
        min_tail_observations=kw.pop("min_tail_observations", 50),
        **kw,
    )


# --- the held-out slice is never read ---


def test_bars_at_or_after_the_boundary_do_not_reach_the_result() -> None:
    bars = make_walk_bars(600)
    boundary = bars[400].timestamp
    result = _study(bars, calibration_end=boundary)

    assert result.last_sample_at < boundary
    assert result.sample_count == 400 - WARMUP


def test_changing_the_held_out_slice_cannot_change_the_result() -> None:
    # The strongest form of the non-contamination claim: replace every bar
    # after the boundary with a wildly different walk and the study must be
    # byte-identical, because it never looked at them.
    bars = make_walk_bars(600)
    boundary = bars[400].timestamp

    altered = bars[:400] + make_walk_bars(
        200, start=bars[400].timestamp, start_price=bars[399].close, seed=999
    )
    assert altered[450] != bars[450]

    first = _study(bars, calibration_end=boundary)
    second = _study(altered, calibration_end=boundary)
    # The dataset fingerprint legitimately differs -- different bars on disk --
    # so compare the measurement itself.
    assert first.model_dump(exclude={"inputs", "study_fingerprint"}) == second.model_dump(
        exclude={"inputs", "study_fingerprint"}
    )


def test_an_empty_calibration_slice_is_refused() -> None:
    bars = make_walk_bars(300)
    with pytest.raises(CalibrationError, match="no sample falls before"):
        _study(bars, calibration_end=bars[0].timestamp)


# --- percentiles: supported only ---


def test_minimum_samples_for_encodes_the_tail_rule() -> None:
    assert minimum_samples_for(10.0, min_tail_observations=50) == 500
    assert minimum_samples_for(5.0, min_tail_observations=50) == 1000
    assert minimum_samples_for(1.0, min_tail_observations=50) == 5000
    # Symmetric: an upper percentile needs what its mirror needs.
    assert minimum_samples_for(99.0, min_tail_observations=50) == 5000


def test_an_unsupportable_percentile_is_omitted_not_estimated() -> None:
    # 280 samples support p25/p75 (needs 200) but not p5/p95 (needs 1000).
    bars = make_walk_bars(300)
    result = _study(bars, percentiles=(5.0, 25.0, 75.0, 95.0))
    reported = [p.percentile for p in result.percentiles]
    assert reported == [25.0, 75.0]
    # ...while the request is still on the record, so the omission is visible.
    assert result.inputs.requested_percentiles == (5.0, 25.0, 75.0, 95.0)


def test_a_larger_sample_unlocks_more_percentiles() -> None:
    result = _study(make_walk_bars(1200), percentiles=(5.0, 25.0, 75.0, 95.0))
    assert [p.percentile for p in result.percentiles] == [5.0, 25.0, 75.0, 95.0]


def test_a_sample_supporting_no_requested_percentile_is_refused() -> None:
    with pytest.raises(CalibrationError, match="support none of the requested percentiles"):
        _study(make_walk_bars(300), percentiles=(1.0, 99.0))


def test_percentiles_are_ascending_and_ordered_by_value() -> None:
    result = _study(make_walk_bars(1200))
    assert [p.percentile for p in result.percentiles] == sorted(PERCENTILES)
    assert [p.value for p in result.percentiles] == sorted(p.value for p in result.percentiles)


def test_the_distribution_brackets_its_own_percentiles() -> None:
    result = _study(make_walk_bars(1200))
    assert result.minimum <= result.percentiles[0].value
    assert result.percentiles[-1].value <= result.maximum
    assert result.minimum <= result.median <= result.maximum


# --- reproducibility ---


def test_identical_inputs_produce_a_byte_identical_result() -> None:
    bars = make_walk_bars(600)
    first, second = _study(bars), _study(bars)
    assert first == second
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )


def test_independently_built_identical_bars_produce_an_identical_study() -> None:
    assert _study(make_walk_bars(600)) == _study(make_walk_bars(600))


@pytest.mark.parametrize(
    "override",
    [
        {"warmup": 25},
        {"percentiles": (10.0, 90.0)},
        {"min_tail_observations": 10},
    ],
)
def test_changing_an_input_changes_the_study_fingerprint(override: dict) -> None:
    bars = make_walk_bars(600)
    assert _study(bars).study_fingerprint != _study(bars, **override).study_fingerprint


def test_the_fingerprint_is_the_canonical_hash_of_the_inputs() -> None:
    result = _study(make_walk_bars(600))
    assert result.study_fingerprint == canonical_hash(result.inputs)
    assert result.inputs.study_version == STUDY_VERSION


def test_a_study_may_not_claim_an_identity_it_does_not_have() -> None:
    result = _study(make_walk_bars(600))
    with pytest.raises(ValidationError, match="does not match these inputs"):
        VolatilityDistribution(
            **{**result.model_dump(), "study_fingerprint": "0" * 64},
        )


def test_the_result_round_trips_through_json() -> None:
    result = _study(make_walk_bars(600))
    assert VolatilityDistribution.model_validate(result.model_dump(mode="json")) == result


def test_the_result_is_frozen_and_closed() -> None:
    result = _study(make_walk_bars(600))
    with pytest.raises(ValidationError):
        result.median = 0.0  # type: ignore[misc]


# --- validation: closures forgiven, holes are not ---


def test_a_weekend_gapped_dataset_is_measured_not_refused() -> None:
    # The first real consumer of Phase 2's TradingSession: six real-shaped gold
    # weeks, five weekly closures, and the study runs.
    bars = make_weekend_bars(weeks=6)
    result = _study(bars, warmup=CONVERGED_WARMUP_BARS)
    assert result.sample_count == len(bars) - CONVERGED_WARMUP_BARS
    assert result.session_boundary_window_count > 0
    assert 0.0 < result.session_boundary_window_fraction < 1.0


def test_a_genuine_mid_week_hole_aborts_the_study() -> None:
    # A vendor hole would inject one enormous true range that Wilder smoothing
    # then spreads across the following readings -- silently distorting the
    # distribution. It must fail closed instead.
    bars = make_walk_bars(300)
    gapped = bars[:150] + make_walk_bars(
        150, start=bars[149].timestamp + timedelta(hours=20), start_price=bars[149].close
    )
    with pytest.raises(CalibrationError, match="not fit to measure"):
        _study(gapped)


def test_a_contiguous_dataset_reports_no_boundary_windows() -> None:
    result = _study(make_walk_bars(600))
    assert result.session_boundary_window_count == 0
    assert result.session_boundary_window_fraction == 0.0


# --- the study proposes nothing ---


def test_the_result_carries_no_threshold_or_classification() -> None:
    # A study that also recommended an answer would make citing it circular.
    fields = set(VolatilityDistribution.model_fields) | set(StudyInputs.model_fields)
    forbidden = {"low_atr_pct", "high_atr_pct", "threshold", "thresholds", "level", "regime"}
    assert not fields & forbidden
    payload = json.dumps(_study(make_walk_bars(600)).model_dump(mode="json")).lower()
    for word in ("low", "medium", "high", "threshold"):
        assert word not in payload


def test_coverage_fields_describe_what_the_numbers_rest_on() -> None:
    bars = make_walk_bars(600)
    result = _study(bars)
    assert result.sample_count == 600 - WARMUP
    assert result.first_sample_at == bars[WARMUP].timestamp
    assert result.last_sample_at == bars[-1].timestamp
    assert result.inputs.symbol == "XAUUSD"
    assert result.inputs.timeframe is Timeframe.H1
    assert result.inputs.atr_length == ATR_LENGTH


def test_the_dataset_fingerprint_is_taken_from_the_verified_dataset() -> None:
    bars = make_walk_bars(600)
    dataset = build_loaded_dataset(bars)
    result = study_atr_percent(
        dataset,
        session=XAUUSD_WEEKLY,
        atr_length=ATR_LENGTH,
        warmup_bars=WARMUP,
        calibration_end=bars[-1].timestamp + timedelta(1),
        percentiles=PERCENTILES,
        quantile_method=QuantileMethod.LINEAR,
        min_tail_observations=50,
    )
    assert result.inputs.dataset_fingerprint == dataset.manifest.fingerprint


# --- inputs discipline ---


def test_inputs_reject_unsorted_or_duplicated_percentiles() -> None:
    base = dict(
        study_version=STUDY_VERSION,
        dataset_fingerprint="a" * 64,
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        atr_length=14,
        warmup_bars=200,
        quantile_method=QuantileMethod.LINEAR,
        min_tail_observations=50,
        calibration_end=make_walk_bars(2)[-1].timestamp,
    )
    with pytest.raises(ValidationError, match="must be ascending"):
        StudyInputs(**base, requested_percentiles=(90.0, 10.0))
    with pytest.raises(ValidationError, match="duplicates"):
        StudyInputs(**base, requested_percentiles=(10.0, 10.0))
    with pytest.raises(ValidationError, match="strictly inside"):
        StudyInputs(**base, requested_percentiles=(0.0, 50.0))


def test_the_quantile_method_is_a_closed_vocabulary() -> None:
    # One member, recorded in the identity: leaving interpolation implicit
    # would make a study reproducible only on the machine that ran it.
    assert [m.value for m in QuantileMethod] == ["linear"]
    assert _study(make_walk_bars(600)).inputs.quantile_method is QuantileMethod.LINEAR


# --- result invariants are enforced, not assumed ---


def _result_payload() -> dict:
    return _study(make_walk_bars(600)).model_dump()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"last_sample_at": make_walk_bars(1)[0].timestamp}, "before first_sample_at"),
        ({"maximum": -1.0}, "below minimum"),
        ({"session_boundary_window_count": 10**6}, "exceeds"),
    ],
)
def test_an_incoherent_result_is_rejected(override: dict, message: str) -> None:
    # The fingerprint still matches its inputs, so these guards are the only
    # thing standing between a hand-assembled result and a plausible-looking
    # distribution that contradicts itself.
    with pytest.raises(ValidationError, match=message):
        VolatilityDistribution(**{**_result_payload(), **override})


def test_out_of_order_percentiles_are_rejected() -> None:
    payload = _result_payload()
    payload["percentiles"] = list(reversed(payload["percentiles"]))
    with pytest.raises(ValidationError, match="must be ascending"):
        VolatilityDistribution(**payload)


def test_inputs_reject_a_naive_calibration_boundary_and_a_short_warmup() -> None:
    base = dict(
        study_version=STUDY_VERSION,
        dataset_fingerprint="a" * 64,
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        quantile_method=QuantileMethod.LINEAR,
        min_tail_observations=50,
        requested_percentiles=(25.0, 75.0),
    )
    boundary = make_walk_bars(2)[-1].timestamp
    with pytest.raises(ValidationError, match="timezone-aware"):
        StudyInputs(
            **base, atr_length=14, warmup_bars=200, calibration_end=boundary.replace(tzinfo=None)
        )
    with pytest.raises(ValidationError, match="must be at least atr_length"):
        StudyInputs(**base, atr_length=14, warmup_bars=13, calibration_end=boundary)


def test_a_single_sample_cannot_support_a_dispersion_statistic() -> None:
    bars = make_walk_bars(300)
    # A boundary admitting exactly one sample past the warm-up.
    with pytest.raises(CalibrationError, match="dispersion statistic is undefined"):
        _study(bars, calibration_end=bars[WARMUP + 1].timestamp)
