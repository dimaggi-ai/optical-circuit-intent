"""When is a retune legal, and what does waiting for a legal moment cost?

This is the question W4 poses: a mirror takes milliseconds to move, and a
collective takes microseconds, so a circuit change cannot simply be applied. It
has to land at a boundary in the job's rhythm that can absorb it.

The model has three parts.

**Quiet window.** At each boundary, how long the stitch carries nothing. A
retune shorter than the quiet window is invisible: the job never knows it
happened.

**Stall.** A retune longer than the quiet window stops the job. That costs
accelerator-seconds --- every rank in the job waits, not just the ones on the
circuit --- and if the stall outlasts the collective watchdog, the job does not
stall, it dies.

**Wait.** A boundary that can absorb the retune is no use until it arrives. A
change legal only between epochs waits, on average, half an epoch.

The result that falls out of putting all three together, and the reason this is
a model rather than a table: **the cheapest legal boundary is usually not the
safest one, and it is never simply the latest one.** Waiting for a boundary with
a longer quiet window buys a smaller stall at the cost of a longer wait, and
which side wins flips as the retune time grows. Where it flips depends on the
job's rhythm, not on the switch.

One coupling is worth stating in advance because it surprises people. Whether a
checkpoint boundary is a good moment to retune depends entirely on whether the
checkpoint crosses the stitch. Write locally and replicate later, and the
checkpoint window is the longest quiet stretch the job has. Replicate
synchronously across the stitch, and it is the busiest. Same job, same switch,
opposite answer --- see :mod:`ocintent.checkpoint`.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .intent import Boundary


class Legality(str, Enum):
    """What happens if the retune is applied at this boundary."""

    #: Fits inside the quiet window. The job never notices.
    INVISIBLE = "invisible"
    #: Longer than the quiet window. The job stalls and survives.
    STALL = "stall"
    #: The stall outlasts the collective watchdog. The job dies.
    FATAL = "fatal"

    @property
    def legal(self) -> bool:
        return self is not Legality.FATAL


@dataclass(frozen=True)
class JobRhythm:
    """The periodic structure a circuit change has to fit into.

    Everything here is about *the job*, not the switch. Two jobs on the same
    plant have different legal boundaries, which is the point: a retune policy
    written in terms of the switch alone cannot be right for both.
    """

    accelerators: int
    #: Wall time of one training step.
    step_s: float
    #: The part of a step during which traffic crosses the stitch. The stitch is
    #: idle for the rest of the step, and that idle time is the quiet window.
    cross_stitch_collective_s: float
    #: Steps between checkpoints.
    steps_per_checkpoint: int
    #: How long a checkpoint takes to write.
    checkpoint_window_s: float
    #: Whether checkpoint traffic crosses the stitch. Flips the checkpoint
    #: boundary between the best and the worst moment to retune.
    checkpoint_crosses_stitch: bool
    #: Steps in an epoch.
    steps_per_epoch: int
    #: Pause at an epoch boundary: validation, reshuffle, whatever the job does.
    epoch_gap_s: float
    #: The collective's watchdog. A stall longer than this kills the job rather
    #: than delaying it.
    collective_timeout_s: float = 600.0
    #: Time left in the job, used for the between-jobs boundary.
    remaining_s: float = 86_400.0

    def __post_init__(self) -> None:
        if self.accelerators < 1:
            raise ValueError("accelerators must be at least 1")
        for name in ("step_s", "checkpoint_window_s", "epoch_gap_s",
                     "collective_timeout_s", "remaining_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.cross_stitch_collective_s < 0:
            raise ValueError("cross_stitch_collective_s must be non-negative")
        if self.cross_stitch_collective_s >= self.step_s:
            raise ValueError(
                "the cross-stitch collective fills or exceeds the whole step, so there "
                "is no quiet window between steps; this job is stitch-bound and a "
                "retune policy is not its problem"
            )
        if self.steps_per_checkpoint < 1 or self.steps_per_epoch < 1:
            raise ValueError("steps_per_checkpoint and steps_per_epoch must be >= 1")

    # -- the three quantities ------------------------------------------------

    def quiet_window_s(self, boundary: Boundary) -> float:
        """How long the stitch carries nothing at this boundary."""
        idle_in_step = self.step_s - self.cross_stitch_collective_s
        if boundary is Boundary.MID_COLLECTIVE:
            return 0.0
        if boundary is Boundary.BETWEEN_STEPS:
            return idle_in_step
        if boundary is Boundary.BETWEEN_CHECKPOINTS:
            # The coupling described in the module docstring. A checkpoint that
            # crosses the stitch does not open a window, it occupies one --- and
            # the surrounding step gap is all that is left.
            if self.checkpoint_crosses_stitch:
                return idle_in_step
            return idle_in_step + self.checkpoint_window_s
        if boundary is Boundary.BETWEEN_EPOCHS:
            window = idle_in_step + self.epoch_gap_s
            if not self.checkpoint_crosses_stitch:
                # Epochs almost always checkpoint, so the two windows abut.
                window += self.checkpoint_window_s
            return window
        if boundary is Boundary.BETWEEN_JOBS:
            # The circuit is free. Nothing constrains the retune's length.
            return float("inf")
        raise ValueError(f"unhandled boundary {boundary!r}")

    def mean_wait_s(self, boundary: Boundary) -> float:
        """Expected wait for the next such boundary, for a request arriving at
        a uniformly random moment: half the period."""
        if boundary is Boundary.MID_COLLECTIVE:
            return 0.0
        if boundary is Boundary.BETWEEN_STEPS:
            return self.step_s / 2.0
        if boundary is Boundary.BETWEEN_CHECKPOINTS:
            return self.steps_per_checkpoint * self.step_s / 2.0
        if boundary is Boundary.BETWEEN_EPOCHS:
            return self.steps_per_epoch * self.step_s / 2.0
        if boundary is Boundary.BETWEEN_JOBS:
            return self.remaining_s / 2.0
        raise ValueError(f"unhandled boundary {boundary!r}")


@dataclass(frozen=True)
class RetuneCost:
    """What applying a retune at one boundary costs.

    ``lost_accelerator_hours`` counts the *whole job*, not the ranks on the
    circuit. A synchronous job stalls together, and costing only the stitched
    ranks is the arithmetic that makes spanning look cheap.
    """

    boundary: Boundary
    legality: Legality
    quiet_window_s: float
    wait_s: float
    stall_s: float
    accelerators: int

    @property
    def lost_accelerator_hours(self) -> float:
        if self.legality is Legality.FATAL:
            return float("inf")
        return self.accelerators * self.stall_s / 3_600.0

    @property
    def total_delay_s(self) -> float:
        """Wall time from asking to the circuit being changed."""
        return self.wait_s + self.stall_s

    def explain(self) -> str:
        if self.legality is Legality.FATAL:
            return (
                f"{self.boundary.value}: the {self.stall_s:.1f}s stall outlasts the "
                "collective watchdog; the job would not pause, it would fail"
            )
        if self.legality is Legality.INVISIBLE:
            return (
                f"{self.boundary.value}: fits inside a {self.quiet_window_s:.2f}s quiet "
                f"window, so the job never notices; average wait {self.wait_s:.1f}s"
            )
        return (
            f"{self.boundary.value}: overruns the {self.quiet_window_s:.2f}s quiet window "
            f"by {self.stall_s:.2f}s, costing {self.lost_accelerator_hours:.2f} "
            f"accelerator-hours; average wait {self.wait_s:.1f}s"
        )


def assess(rhythm: JobRhythm, retune_s: float, boundary: Boundary) -> RetuneCost:
    """Cost and legality of applying a retune of this length at this boundary."""
    if retune_s < 0:
        raise ValueError("retune_s must be non-negative")
    quiet = rhythm.quiet_window_s(boundary)
    stall = 0.0 if retune_s <= quiet else retune_s - quiet
    if stall <= 0:
        legality = Legality.INVISIBLE
    elif stall > rhythm.collective_timeout_s:
        legality = Legality.FATAL
    else:
        legality = Legality.STALL
    return RetuneCost(
        boundary=boundary,
        legality=legality,
        quiet_window_s=quiet,
        wait_s=rhythm.mean_wait_s(boundary),
        stall_s=stall,
        accelerators=rhythm.accelerators,
    )


def ladder(rhythm: JobRhythm, retune_s: float) -> Tuple[RetuneCost, ...]:
    """Every boundary, in order of increasing rarity."""
    return tuple(assess(rhythm, retune_s, b) for b in sorted(Boundary, key=lambda b: b.rank))


def _selectable(rhythm: JobRhythm, retune_s: float) -> List[RetuneCost]:
    """Legal boundaries a controller may actually choose.

    ``MID_COLLECTIVE`` is excluded. It is legal only for a retune of length
    exactly zero, and no switch is instantaneous, so leaving it in lets a
    degenerate case win every selection. It stays in :func:`ladder` because the
    row is the reason the rest of the module exists: it is where a naive
    controller would apply the change.
    """
    return [
        c for c in ladder(rhythm, retune_s)
        if c.legality.legal and c.boundary is not Boundary.MID_COLLECTIVE
    ]


def cheapest_legal(rhythm: JobRhythm, retune_s: float) -> Optional[RetuneCost]:
    """The boundary that costs the fewest accelerator-hours.

    Waiting costs no accelerator-hours --- the job runs productively while it
    waits --- so this selector will happily wait twelve hours to avoid a sixty
    second stall. That is the right answer when the retune is optional, and the
    wrong one when it is a repair. Use :func:`soonest_legal` for the second case
    and read :func:`objectives_disagree` before choosing.

    Ties are broken by the shorter wait, then by the earlier boundary.

    Returns ``None`` when every boundary is fatal, which happens when the retune
    outlasts the watchdog even with the circuit idle. That is not a scheduling
    problem, it is a plant that cannot be reconfigured under load, and saying so
    is more useful than returning the least bad option.
    """
    legal = _selectable(rhythm, retune_s)
    if not legal:
        return None
    return min(legal, key=lambda c: (c.lost_accelerator_hours, c.wait_s, c.boundary.rank))


def soonest_legal(rhythm: JobRhythm, retune_s: float) -> Optional[RetuneCost]:
    """The boundary that changes the circuit soonest, wait plus stall.

    The selector to use when the retune is a repair: a failover away from a
    circuit whose error rate is climbing, where every second spent waiting is a
    second the job is running on a path that may not survive the wait.

    It will accept a stall to avoid a wait, which is exactly what
    :func:`cheapest_legal` refuses to do.
    """
    legal = _selectable(rhythm, retune_s)
    if not legal:
        return None
    return min(legal, key=lambda c: (c.total_delay_s, c.lost_accelerator_hours, c.boundary.rank))


def objectives_disagree(rhythm: JobRhythm, retune_s: float) -> Optional[Dict[str, object]]:
    """Where the two selectors pick different boundaries, and what it costs.

    This is the useful output of the whole module. The two objectives are both
    defensible and they diverge by orders of magnitude on realistic rhythms, so
    a controller that implements only one of them is quietly making a policy
    decision on the operator's behalf.

    Returns ``None`` when they agree.
    """
    cheap = cheapest_legal(rhythm, retune_s)
    soon = soonest_legal(rhythm, retune_s)
    if cheap is None or soon is None or cheap.boundary is soon.boundary:
        return None
    return {
        "cheapest": cheap,
        "soonest": soon,
        "extra_wait_s": cheap.total_delay_s - soon.total_delay_s,
        "extra_accelerator_hours": soon.lost_accelerator_hours - cheap.lost_accelerator_hours,
    }


def _breakpoints(rhythm: JobRhythm) -> List[float]:
    """Every retune length at which the selection can possibly change.

    Both selectors are piecewise linear in the retune length, so the boundary
    each one picks is constant between a finite set of breakpoints. Enumerating
    them exactly is what lets :func:`disagreement_intervals` decompose the
    answer with no grid and no tolerance --- and grids and tolerances are how a
    result ends up depending on a spacing chosen once the answer was known.

    Three families, and that is all there are:

    * ``quiet_b`` --- where boundary *b* stops being invisible and starts to stall.
    * ``quiet_b + timeout`` --- where *b*'s stall outlasts the watchdog and *b*
      becomes fatal.
    * ``quiet_b + wait_c - wait_b`` --- where *b*, now stalling, has its total
      delay overtaken by *c*, which is not yet stalling. Once two boundaries are
      both stalling their delays grow at the same rate, so no further crossing
      is possible, which is why the list is finite.
    """
    finite = [b for b in Boundary if math.isfinite(rhythm.quiet_window_s(b))]
    points = {0.0}
    for b in finite:
        quiet_b = rhythm.quiet_window_s(b)
        points.add(quiet_b)
        points.add(quiet_b + rhythm.collective_timeout_s)
        for c in Boundary:
            crossing = quiet_b + rhythm.mean_wait_s(c) - rhythm.mean_wait_s(b)
            if crossing > 0:
                points.add(crossing)
    return sorted(points)


def disagreement_intervals(
    rhythm: JobRhythm,
) -> Tuple[Tuple[float, float], ...]:
    """Exactly the retune lengths where the two objectives pick different boundaries.

    Returned as a tuple of half-open intervals, which is usually but *not
    always* a single one. Around a third of realistic rhythms produce two or
    more disjoint intervals, because both selectors jump between boundaries more
    than once as the retune grows and the jumps do not line up. An earlier
    version of this function bisected for a single band and returned a number
    that looked like an edge and was not one for those rhythms; DECISIONS.md D4
    records that.

    A final interval ending at infinity means the objectives never reconverge.
    """
    points = _breakpoints(rhythm)
    probes: List[Tuple[float, float, bool]] = []
    for lo, hi in zip(points, points[1:]):
        if hi <= lo:
            continue
        probes.append((lo, hi, objectives_disagree(rhythm, (lo + hi) / 2.0) is not None))
    tail_start = points[-1]
    probes.append((tail_start, math.inf,
                   objectives_disagree(rhythm, tail_start * 2.0 + 1.0) is not None))

    intervals: List[Tuple[float, float]] = []
    for lo, hi, disagrees in probes:
        if not disagrees:
            continue
        if intervals and math.isclose(intervals[-1][1], lo, rel_tol=1e-12, abs_tol=1e-12):
            intervals[-1] = (intervals[-1][0], hi)
        else:
            intervals.append((lo, hi))
    return tuple(intervals)


def disagreement_width_s(rhythm: JobRhythm) -> float:
    """Total measure of the disagreement set, or infinity if it is unbounded.

    The single number worth quoting for a job: how wide a range of retune times
    forces a policy decision that this module cannot make for you.
    """
    total = 0.0
    for lo, hi in disagreement_intervals(rhythm):
        if math.isinf(hi):
            return math.inf
        total += hi - lo
    return total
