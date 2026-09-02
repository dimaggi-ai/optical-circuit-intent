"""The intent language: request, hold-until, release, failover-to.

Four verbs. A job says what it needs from the circuit plane and for how long,
and something else decides whether and when that is legal. Keeping those two
apart is the entire design: an intent is a *statement of need*, and it compiles
to vendor calls only after a legality check has said which boundary can absorb
the change.

The specification is blunt about the boundary of this work, and the code holds
that line: DIMAGGI's addition is the contract and the useful-capacity number,
not a better mirror controller. So an intent compiles to a **plan** --- an
ordered list of vendor operations with the arguments each needs --- and the plan
is handed back to the caller rather than executed. Nothing in this repository
turns a mirror.

An intent is inert and immutable. It knows what it wants and when it wants it,
and nothing about whether it can have it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class Verb(str, Enum):
    """The four things a job can say to the circuit plane."""

    REQUEST = "request"
    HOLD_UNTIL = "hold-until"
    RELEASE = "release"
    FAILOVER_TO = "failover-to"


class Boundary(str, Enum):
    """Points in a job's execution where a circuit change could land.

    Ordered from the most frequent opportunity to the least. A retune that can
    only be absorbed at a rarer boundary waits longer, and the wait is the cost
    that :mod:`ocintent.legality` puts a number on.
    """

    MID_COLLECTIVE = "mid-collective"
    BETWEEN_STEPS = "between-steps"
    BETWEEN_CHECKPOINTS = "between-checkpoints"
    BETWEEN_EPOCHS = "between-epochs"
    BETWEEN_JOBS = "between-jobs"

    @property
    def rank(self) -> int:
        return _BOUNDARY_RANK[self]


_BOUNDARY_RANK = {
    Boundary.MID_COLLECTIVE: 0,
    Boundary.BETWEEN_STEPS: 1,
    Boundary.BETWEEN_CHECKPOINTS: 2,
    Boundary.BETWEEN_EPOCHS: 3,
    Boundary.BETWEEN_JOBS: 4,
}


@dataclass(frozen=True)
class Endpoint:
    """One end of a circuit: a hall and a port on that hall's switch."""

    hall_id: str
    port: str

    def __post_init__(self) -> None:
        if not self.hall_id or not self.port:
            raise ValueError("an endpoint needs both a hall and a port")

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.hall_id}:{self.port}"


@dataclass(frozen=True)
class Intent:
    """One statement of need against the circuit plane.

    ``hold_s`` is what makes this a contract rather than a request. A circuit
    held for an unstated duration is a circuit the scheduler cannot plan around,
    and the fragmentation that follows is the failure W4 calls first-class.
    """

    verb: Verb
    circuit_id: str
    #: Both ends, for REQUEST and FAILOVER_TO. Empty for RELEASE.
    endpoints: Tuple[Endpoint, ...] = ()
    #: How long the circuit is needed, in seconds. Required for REQUEST and
    #: HOLD_UNTIL; meaningless for RELEASE.
    hold_s: Optional[float] = None
    #: Minimum width the job can work with, in Gbit/s.
    min_bw_gbps: float = 0.0
    #: The circuit to fail over from. FAILOVER_TO only.
    replaces: Optional[str] = None
    #: The job this intent belongs to, carried into the plan and the ledger.
    job_id: str = ""
    #: Free-form; carried through untouched.
    labels: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.circuit_id:
            raise ValueError("every intent names a circuit")
        if self.min_bw_gbps < 0:
            raise ValueError("min_bw_gbps must be non-negative")
        if self.hold_s is not None and self.hold_s <= 0:
            raise ValueError("hold_s must be positive when given")

        if self.verb in (Verb.REQUEST, Verb.FAILOVER_TO):
            if len(self.endpoints) != 2:
                raise ValueError(f"{self.verb.value} needs exactly two endpoints")
            if self.endpoints[0] == self.endpoints[1]:
                raise ValueError("a circuit's two endpoints must differ")
            if self.endpoints[0].hall_id == self.endpoints[1].hall_id:
                raise ValueError(
                    "both endpoints are in the same hall; that is a patch cable, "
                    "not a stitch, and this plane should not be asked for it"
                )
        if self.verb is Verb.REQUEST and self.hold_s is None:
            raise ValueError(
                "a request with no hold time is a circuit the scheduler cannot plan "
                "around; state how long the job needs it"
            )
        if self.verb is Verb.HOLD_UNTIL and self.hold_s is None:
            raise ValueError("hold-until needs a duration")
        if self.verb is Verb.FAILOVER_TO and not self.replaces:
            raise ValueError("failover-to must name the circuit it replaces")
        if self.verb is Verb.RELEASE and self.endpoints:
            raise ValueError("release names a circuit, not endpoints")

    @property
    def halls(self) -> Tuple[str, ...]:
        return tuple(sorted({e.hall_id for e in self.endpoints}))

    @property
    def disruptive(self) -> bool:
        """Whether carrying this out interrupts traffic already flowing.

        A release and a failover both take a live path away. A request adds one.
        The distinction decides whether a legality check has to run at all.
        """
        return self.verb in (Verb.RELEASE, Verb.FAILOVER_TO)

    def canonical(self) -> str:
        payload = {
            "verb": self.verb.value,
            "circuit_id": self.circuit_id,
            "endpoints": [[e.hall_id, e.port] for e in self.endpoints],
            "hold_s": self.hold_s,
            "min_bw_gbps": self.min_bw_gbps,
            "replaces": self.replaces,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["verb"] = self.verb.value
        d["endpoints"] = [{"hall_id": e.hall_id, "port": e.port} for e in self.endpoints]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Intent":
        d = dict(d)
        d["verb"] = Verb(d["verb"])
        d["endpoints"] = tuple(Endpoint(e["hall_id"], e["port"]) for e in d.get("endpoints", []))
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown intent fields: {sorted(unknown)}")
        return cls(**d)

    def replace(self, **changes: Any) -> "Intent":
        return replace(self, **changes)


@dataclass(frozen=True)
class Operation:
    """One vendor call, named and argued but not made."""

    call: str
    args: Dict[str, Any]

    def render(self) -> str:
        rendered = ", ".join(f"{k}={v!r}" for k, v in sorted(self.args.items()))
        return f"{self.call}({rendered})"


@dataclass(frozen=True)
class Plan:
    """What an intent compiles to: operations, in order, and nothing executed.

    A plan is returned, printed, or handed to whatever owns the vendor session.
    This repository has no vendor session. That is deliberate --- see
    DECISIONS.md D2 --- and it is why the operation names below are generic
    rather than any particular controller's.
    """

    intent: Intent
    operations: Tuple[Operation, ...]
    #: The boundary this plan must be executed at, once legality has been run.
    boundary: Optional[Boundary] = None
    notes: Tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"# {self.intent.verb.value} {self.intent.circuit_id}"]
        if self.boundary is not None:
            lines.append(f"# execute at: {self.boundary.value}")
        lines += [op.render() for op in self.operations]
        lines += [f"# note: {n}" for n in self.notes]
        return "\n".join(lines)


def compile_intent(intent: Intent, *, boundary: Optional[Boundary] = None) -> Plan:
    """Turn an intent into an ordered plan of vendor operations.

    The operation vocabulary is deliberately generic: ``reserve_ports``,
    ``cross_connect``, ``verify_path``, ``teardown``. Every optical controller
    worth binding to has these four under some name, and naming them after one
    vendor's API would make the plan look portable while being anything but.

    Ordering is the part that matters and is not obvious. A failover builds the
    replacement path and verifies it *before* tearing the old one down, because
    the reverse order turns a degraded circuit into no circuit for the duration
    of the retune. That is the whole point of having a failover verb.
    """
    ops: List[Operation] = []
    notes: List[str] = []
    i = intent

    if i.verb is Verb.REQUEST:
        ops.append(Operation("reserve_ports", {
            "circuit_id": i.circuit_id,
            "endpoints": [str(e) for e in i.endpoints],
            "min_bw_gbps": i.min_bw_gbps,
            "hold_s": i.hold_s,
        }))
        ops.append(Operation("cross_connect", {
            "circuit_id": i.circuit_id,
            "a": str(i.endpoints[0]), "z": str(i.endpoints[1]),
        }))
        ops.append(Operation("verify_path", {
            "circuit_id": i.circuit_id, "expect_min_bw_gbps": i.min_bw_gbps,
        }))
        notes.append(
            "verify_path is not optional. A cross-connect that returns success and a "
            "path that carries traffic are different claims, and the gap between them "
            "is what the drift ledger measures."
        )

    elif i.verb is Verb.HOLD_UNTIL:
        ops.append(Operation("extend_hold", {"circuit_id": i.circuit_id, "hold_s": i.hold_s}))
        notes.append(
            "an extension can be refused by the port scheduler if the ports are already "
            "promised; treat a refusal as a planning input, not an error"
        )

    elif i.verb is Verb.RELEASE:
        ops.append(Operation("teardown", {"circuit_id": i.circuit_id}))
        ops.append(Operation("release_ports", {"circuit_id": i.circuit_id}))

    elif i.verb is Verb.FAILOVER_TO:
        # Build first, verify, then tear down. See the docstring.
        ops.append(Operation("reserve_ports", {
            "circuit_id": i.circuit_id,
            "endpoints": [str(e) for e in i.endpoints],
            "min_bw_gbps": i.min_bw_gbps,
            "hold_s": i.hold_s,
        }))
        ops.append(Operation("cross_connect", {
            "circuit_id": i.circuit_id,
            "a": str(i.endpoints[0]), "z": str(i.endpoints[1]),
        }))
        ops.append(Operation("verify_path", {
            "circuit_id": i.circuit_id, "expect_min_bw_gbps": i.min_bw_gbps,
        }))
        ops.append(Operation("teardown", {"circuit_id": i.replaces}))
        ops.append(Operation("release_ports", {"circuit_id": i.replaces}))
        notes.append(
            f"the replacement is verified before {i.replaces!r} is torn down; the "
            "reverse order converts a degraded circuit into no circuit"
        )
        notes.append(
            "this needs both paths' ports free at once. If the radix cannot hold both, "
            "the failover is a retune and must be scheduled as one"
        )

    return Plan(intent=i, operations=tuple(ops), boundary=boundary, notes=tuple(notes))
