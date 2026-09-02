"""Checkpoint traffic and collective traffic want the same circuit.

W9 states the problem in one line: a stitch busy with a collective cannot also
move multi-terabyte weights. This module puts a number on that, for the four
cross-hall checkpoint strategies an operator actually chooses between.

The result worth reading twice is that **the strategy with the shortest
checkpoint window is often the most expensive one.** Asynchronous replication
takes the write off the critical path, so the job stops for the local write
only --- and then spends the following minutes pushing terabytes across the
same circuit the collectives need, inflating every step it overlaps. Whether
that is cheaper than simply stopping for a synchronous replication depends on
the ratio of state size to circuit width, and it flips inside the range of
plants that exist.

The tax has two parts and they are reported separately, because they are paid
by different people. The **stop tax** is time the job is not computing; it shows
up in a training curve. The **contention tax** is steps that ran slower than
they should have; it shows up nowhere at all unless someone is looking for it,
which is the reason to compute it.

Durability is not a tax and is not netted off. Writing locally is cheapest and
loses everything if the hall does, and this module reports the exposure rather
than converting it into seconds --- see DECISIONS.md D5.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Dict, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from .legality import JobRhythm

GIGABIT = 1e9
GIGABYTE = 1e9


class Strategy(str, Enum):
    """How a job's checkpoint state gets out of the hall it was computed in."""

    #: Write to hall-local storage. Nothing crosses. Lost with the hall.
    WRITE_LOCAL = "write-local"
    #: Write local, then push a copy across the stitch in the background.
    ASYNC_REPLICATE = "async-replicate"
    #: Write local and across the stitch before the job resumes.
    SYNC_REPLICATE = "sync-replicate"
    #: Write straight to an object store reached across the stitch.
    STAGE_THROUGH_OBJECT = "stage-through-object"

    @property
    def crosses_stitch(self) -> bool:
        return self is not Strategy.WRITE_LOCAL

    @property
    def on_critical_path(self) -> bool:
        """Whether the crossing happens while the job is stopped."""
        return self in (Strategy.SYNC_REPLICATE, Strategy.STAGE_THROUGH_OBJECT)

    @property
    def survives_hall_loss(self) -> bool:
        return self is not Strategy.WRITE_LOCAL


@dataclass(frozen=True)
class CheckpointPlan:
    """A job's checkpoint state, and the pipes available to move it."""

    strategy: Strategy
    #: Total checkpoint state, bytes.
    state_bytes: float
    #: Hall-local storage write bandwidth available to this job, GB/s.
    local_write_GBps: float
    #: The stitch's aggregate width, Gbit/s. Shared, not per-rank.
    stitch_bw_gbps: float
    #: Share of the stitch a *background* replication is allowed to take, in
    #: (0, 1]. It does not apply to a transfer that happens while the job is
    #: stopped: there is no collective to share with then, so the critical-path
    #: strategies get the whole circuit.
    stitch_share: float = 1.0
    #: Rate the *remote* destination will accept, GB/s: the object store's
    #: ingest budget, or the far hall's write bandwidth. Caps the transfer
    #: alongside the circuit, and binds instead of the circuit whenever the
    #: destination is slower than the glass --- which for an object store it
    #: usually is. It does not touch the local write: an object store's ingest
    #: rate has no bearing on how fast a hall-local filesystem accepts bytes,
    #: and an earlier draft that applied it there made ``write-local`` respond
    #: to a field it does not use.
    ingest_budget_GBps: Optional[float] = None

    def __post_init__(self) -> None:
        if self.state_bytes <= 0:
            raise ValueError("state_bytes must be positive")
        if self.local_write_GBps <= 0:
            raise ValueError("local_write_GBps must be positive")
        if self.stitch_bw_gbps <= 0:
            raise ValueError("stitch_bw_gbps must be positive")
        if not 0.0 < self.stitch_share <= 1.0:
            raise ValueError("stitch_share must be in (0, 1]")
        if self.ingest_budget_GBps is not None and self.ingest_budget_GBps <= 0:
            raise ValueError("ingest_budget_GBps must be positive when given")

    @property
    def local_write_s(self) -> float:
        return self.state_bytes / (self.local_write_GBps * GIGABYTE)

    @property
    def effective_stitch_GBps(self) -> float:
        """Transfer rate across the stitch: the slower of glass and destination."""
        share = 1.0 if self.strategy.on_critical_path else self.stitch_share
        circuit_GBps = self.stitch_bw_gbps * share / 8.0
        if self.ingest_budget_GBps is None:
            return circuit_GBps
        return min(circuit_GBps, self.ingest_budget_GBps)

    @property
    def stitch_transfer_s(self) -> float:
        """Seconds of circuit time the state needs.

        A transfer on the critical path gets the whole circuit, because the job
        is stopped and no collective is competing for it. A background transfer
        gets only ``stitch_share``. Applying the share to both was a bug in an
        earlier draft and it made the critical-path strategies look worse than
        they are by exactly the reciprocal of the share.
        """
        if not self.strategy.crosses_stitch:
            return 0.0
        return self.state_bytes / (self.effective_stitch_GBps * GIGABYTE)

    @property
    def stop_s(self) -> float:
        """How long the job is stopped: the checkpoint window proper."""
        if self.strategy is Strategy.WRITE_LOCAL:
            return self.local_write_s
        if self.strategy is Strategy.ASYNC_REPLICATE:
            return self.local_write_s
        if self.strategy is Strategy.SYNC_REPLICATE:
            # Local and remote writes proceed together; the job waits for both.
            return max(self.local_write_s, self.stitch_transfer_s)
        if self.strategy is Strategy.STAGE_THROUGH_OBJECT:
            # Nothing lands locally; the object store is across the stitch.
            return self.stitch_transfer_s
        raise ValueError(f"unhandled strategy {self.strategy!r}")

    @property
    def background_stitch_s(self) -> float:
        """Circuit time spent while the job is running, and contending."""
        if self.strategy is Strategy.ASYNC_REPLICATE:
            return self.stitch_transfer_s
        return 0.0


@dataclass(frozen=True)
class Tax:
    """What a checkpoint strategy costs, split by who notices."""

    strategy: Strategy
    stop_s: float
    background_stitch_s: float
    #: Fraction of wall time the job spends stopped for checkpoints.
    stop_fraction: float
    #: Fraction of wall time lost to steps that ran slow because the circuit was
    #: shared with a background replication.
    contention_fraction: float
    #: Steps affected by contention, per checkpoint period.
    contended_steps: float
    survives_hall_loss: bool

    @property
    def total_fraction(self) -> float:
        return self.stop_fraction + self.contention_fraction

    def explain(self) -> str:
        durability = "survives hall loss" if self.survives_hall_loss else "lost with the hall"
        return (
            f"{self.strategy.value}: stops the job {self.stop_fraction * 100:.2f}% of the "
            f"time and slows {self.contended_steps:.0f} steps per checkpoint for another "
            f"{self.contention_fraction * 100:.2f}%, total {self.total_fraction * 100:.2f}%; "
            f"{durability}"
        )


def tax(plan: CheckpointPlan, rhythm: "JobRhythm") -> Tax:
    """Price a checkpoint strategy against a job's rhythm.

    The :class:`~ocintent.legality.JobRhythm` import is deferred to type-checking
    only: nothing here calls into the legality model at runtime, and keeping the
    runtime edge absent means a change to the retune ladder cannot quietly change
    a checkpoint number.

    The contention model, stated plainly so it can be argued with: while a
    background replication is running it takes ``stitch_share`` of the circuit,
    so a collective that needed ``t`` seconds of the circuit now needs
    ``t / (1 - stitch_share)``. The inflation applies only to the steps the
    replication actually overlaps. This treats the circuit as a fluid shared in
    fixed proportion, which is the same approximation the atlas in
    ``network-vs-more-gpus`` makes for an aggregate circuit, and it ignores
    queueing --- so it is a floor on the contention cost, not an estimate of it.
    """
    period_s = rhythm.steps_per_checkpoint * rhythm.step_s + plan.stop_s

    stop_fraction = plan.stop_s / period_s

    contended_steps = 0.0
    contention_fraction = 0.0
    if plan.background_stitch_s > 0 and plan.stitch_share < 1.0:
        inflation_per_step = rhythm.cross_stitch_collective_s * (
            plan.stitch_share / (1.0 - plan.stitch_share)
        )
        steps_covered = plan.background_stitch_s / rhythm.step_s
        contended_steps = min(steps_covered, float(rhythm.steps_per_checkpoint))
        contention_fraction = (contended_steps * inflation_per_step) / period_s
    elif plan.background_stitch_s > 0:
        # A background replication allowed the whole circuit leaves nothing for
        # the collective. Report the steps as fully stalled rather than divide
        # by zero and call it infinity.
        contended_steps = min(
            plan.background_stitch_s / rhythm.step_s, float(rhythm.steps_per_checkpoint)
        )
        contention_fraction = (contended_steps * rhythm.step_s) / period_s

    return Tax(
        strategy=plan.strategy,
        stop_s=plan.stop_s,
        background_stitch_s=plan.background_stitch_s,
        stop_fraction=stop_fraction,
        contention_fraction=contention_fraction,
        contended_steps=contended_steps,
        survives_hall_loss=plan.strategy.survives_hall_loss,
    )


def compare(plan: CheckpointPlan, rhythm: "JobRhythm") -> Dict[Strategy, Tax]:
    """Price every strategy against the same state, pipes and rhythm."""
    from dataclasses import replace as _replace

    return {
        s: tax(_replace(plan, strategy=s), rhythm)
        for s in Strategy
    }


def cheapest_durable(plan: CheckpointPlan, rhythm: "JobRhythm") -> Tax:
    """The cheapest strategy whose checkpoint survives losing the hall.

    Separated from an unqualified "cheapest" on purpose. Writing locally is
    almost always the cheapest thing to do and it is not a checkpoint strategy
    for a job spread across halls --- it is a bet that the hall holding the
    state is the one that will not fail. An operator may take that bet, but a
    function called ``cheapest`` should not take it for them.
    """
    durable = [t for t in compare(plan, rhythm).values() if t.survives_hall_loss]
    return min(durable, key=lambda t: t.total_fraction)


# --------------------------------------------------------------------------
# the fidelity rule
# --------------------------------------------------------------------------


class StallCause(str, Enum):
    """What stopped the job."""

    STORAGE = "storage"
    FABRIC = "fabric"
    #: Both signatures present, or neither. Not a guess.
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class StallEvidence:
    """What was observed when the job stopped making progress."""

    #: The filesystem's own latency went up.
    pfs_latency_elevated: bool = False
    #: Storage clients reported queue depth or retries climbing.
    pfs_queue_depth_elevated: bool = False
    #: Link-level errors on the fabric.
    fabric_link_errors: bool = False
    #: The circuit's measured error rate moved.
    circuit_ber_elevated: bool = False
    #: Ranks not on the stitch stalled too.
    non_stitched_ranks_stalled: bool = False
    #: The stall coincided with a checkpoint window.
    during_checkpoint_window: bool = False

    # ``non_stitched_ranks_stalled`` is deliberately *not* used to choose
    # between storage and fabric. In a synchronous job every rank waits at the
    # same barrier, so both failures propagate to every rank and the signal
    # separates neither. An earlier draft read it as evidence for fabric on the
    # reasoning that "a circuit fault propagates and a storage stall does not",
    # which is simply false under a barrier. It survives as a field because it
    # is informative in one case --- when nothing else is lit --- and
    # DECISIONS.md D7 records why it was demoted rather than deleted.


def classify_stall(evidence: StallEvidence) -> Tuple[StallCause, str]:
    """A PFS stall is not an ICI break. Say which, or say neither.

    W9's fidelity rule, implemented as a refusal rather than a heuristic. The
    two failures look identical from the training loop --- the job stops making
    progress --- and conflating them sends an operator to rebuild a fabric that
    was never broken, or to blame a filesystem for a dirty connector.

    Where the evidence points both ways, or neither, this returns AMBIGUOUS. It
    does not pick the more likely one. A confident wrong answer here costs more
    than an honest shrug, because a confident answer is the one that gets acted
    on at three in the morning.
    """
    storage = (
        evidence.pfs_latency_elevated
        or evidence.pfs_queue_depth_elevated
        or evidence.during_checkpoint_window
    )
    fabric = (
        evidence.fabric_link_errors
        or evidence.circuit_ber_elevated
    )

    if evidence.non_stitched_ranks_stalled and not fabric and not storage:
        return (
            StallCause.AMBIGUOUS,
            "ranks that never touch the stitch stalled, with no storage and no fabric "
            "signature; whatever this is, it is not the circuit and it is not the "
            "checkpoint, and looking harder at either will waste the night",
        )
    if storage and fabric:
        return (
            StallCause.AMBIGUOUS,
            "both signatures are present; a degrading circuit can slow a replication "
            "and a slow replication can look like a degrading circuit, and picking one "
            "here would be a guess dressed as a diagnosis",
        )
    if storage:
        return (
            StallCause.STORAGE,
            "storage-side latency or queue depth moved with no fabric error; this is a "
            "filesystem stall and rebuilding the fabric will not fix it",
        )
    if fabric:
        return (
            StallCause.FABRIC,
            "fabric or circuit errors with no storage signature",
        )
    return (
        StallCause.AMBIGUOUS,
        "neither signature is present; the job stopped for a reason this evidence does "
        "not contain, and naming a layer anyway is how the wrong team gets paged",
    )
