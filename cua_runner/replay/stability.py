"""Optional stretch feature: replay a capability N times and report a
stability/flakiness signal (Section 8).

Important distinction this module has to make correctly: for an
IRREVERSIBLE capability, business state changes with every successful
call (e.g. a sub-account cap being reached), so re-running with IDENTICAL
params will legitimately start returning a different (but still correct)
business outcome -- that is not flakiness, it's the business rule working.
Genuine flakiness is a HARD_FAILURE or an inconsistent drift signal on
what should be a deterministic locator resolution. This module reports
both, but only counts hard failures as the flakiness signal.
"""
from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass, field

from cua_runner.replay.engine import replay
from cua_runner.replay.results import ReplayResult, ReplayStatus


@dataclass
class StabilityReport:
    n: int
    success_count: int = 0
    business_outcome_counts: dict = field(default_factory=dict)
    hard_failure_count: int = 0
    hard_failure_details: list = field(default_factory=list)
    drift_signal_runs: int = 0
    results: list = field(default_factory=list)

    @property
    def flaky(self) -> bool:
        """The honest signal: only hard failures count as flakiness.
        Business outcomes varying across runs (e.g. hitting a cap) is
        expected, correct behavior, not instability."""
        return self.hard_failure_count > 0


def run_stability_test(artifact_path: str, param_pool: list[dict], n: int,
                        force_unattended: bool = False) -> StabilityReport:
    report = StabilityReport(n=n)
    business_counter: Counter = Counter()
    pool_cycle = itertools.cycle(param_pool)

    for i in range(n):
        params = dict(next(pool_cycle))
        if "nickname" in params:
            params["nickname"] = f"{params['nickname']} #{i+1}"  # avoid duplicate-nickname collisions across runs

        result: ReplayResult = replay(artifact_path, params, force_unattended=force_unattended)
        report.results.append(result)

        if result.status == ReplayStatus.SUCCESS:
            report.success_count += 1
        elif result.status == ReplayStatus.BUSINESS_OUTCOME:
            business_counter[result.business_result] += 1
        elif result.status == ReplayStatus.HARD_FAILURE:
            report.hard_failure_count += 1
            report.hard_failure_details.append({
                "run": i + 1, "params": params, "message": result.message,
                "debug": result.debug.__dict__ if result.debug else None,
            })

        if result.drift_signals:
            report.drift_signal_runs += 1

    report.business_outcome_counts = dict(business_counter)
    return report
