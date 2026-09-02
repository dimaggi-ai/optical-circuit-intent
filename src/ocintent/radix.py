"""Packing circuits onto a finite optical switch, and the ports that strand.

W4 calls port fragmentation a first-class failure, and it is worth being precise
about why, because the obvious reading is wrong. An optical cross-connect is a
crossbar: any port can reach any other, so there is no fragmentation *within* a
switch in the sense a memory allocator would mean.

The fragmentation is between **trunks**. A switch's ports are not
interchangeable in practice, because a port is spliced onto a particular fibre
bundle going to a particular place. Twelve free ports on the trunk to hall C do
nothing for a job that needs two ports on the trunk to hall B. Free capacity
that cannot serve the demand that exists is stranded capacity, and an operator
reading a switch-level "62% free" number will not see it.

So the metric here is not a packing efficiency. It is: **of the ports that are
free, how many can serve something that is actually being asked for.** That
number requires a demand profile, and a fragmentation figure quoted without one
is not measuring anything.

Widths matter too. A request for 800 Gbit/s on a trunk of 400 Gbit/s ports needs
two ports, and two ports on two different trunks are no use at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Trunk:
    """A fibre bundle from this switch to one peer hall."""

    peer_hall: str
    ports: int
    port_bw_gbps: float

    def __post_init__(self) -> None:
        if not self.peer_hall:
            raise ValueError("a trunk needs a peer hall")
        if self.ports < 1:
            raise ValueError("a trunk needs at least one port")
        if self.port_bw_gbps <= 0:
            raise ValueError("port_bw_gbps must be positive")

    def ports_for(self, bw_gbps: float) -> int:
        """Ports needed to carry this width. Rounds up; there are no half ports."""
        if bw_gbps <= 0:
            return 0
        return int(math.ceil(bw_gbps / self.port_bw_gbps))


@dataclass(frozen=True)
class Request:
    """A job asking for a circuit to one peer hall."""

    circuit_id: str
    peer_hall: str
    bw_gbps: float
    #: Higher preempts lower. Equal priorities never preempt each other.
    priority: int = 0
    job_id: str = ""

    def __post_init__(self) -> None:
        if self.bw_gbps <= 0:
            raise ValueError("a request must ask for some bandwidth")


@dataclass(frozen=True)
class Allocation:
    """Ports actually held by a circuit."""

    circuit_id: str
    peer_hall: str
    ports: int
    priority: int = 0
    job_id: str = ""


class RadixExhausted(Exception):
    """Raised when a request cannot be satisfied and preemption was not asked for."""


@dataclass
class OpticalSwitch:
    """One hall's switch: its trunks and what is currently allocated."""

    hall_id: str
    trunks: Tuple[Trunk, ...]
    allocations: Dict[str, Allocation] = field(default_factory=dict)

    def __post_init__(self) -> None:
        seen = set()
        for trunk in self.trunks:
            if trunk.peer_hall in seen:
                raise ValueError(
                    f"two trunks to {trunk.peer_hall!r}; merge them, or the port counts "
                    "will not add up the way an operator expects"
                )
            seen.add(trunk.peer_hall)
        if self.hall_id in seen:
            raise ValueError("a trunk cannot lead to the hall it starts in")

    # -- inspection ----------------------------------------------------------

    def trunk(self, peer_hall: str) -> Trunk:
        for t in self.trunks:
            if t.peer_hall == peer_hall:
                return t
        raise KeyError(f"{self.hall_id} has no trunk to {peer_hall!r}")

    def total_ports(self, peer_hall: Optional[str] = None) -> int:
        """Ports on one trunk, or on the whole switch.

        A method rather than a property, and deliberately the same shape as
        :meth:`used_ports` and :meth:`free_ports`. When one of the three was a
        property and the other two were not, the first caller outside this
        module formatted a bound method into a report and Python did not
        complain. Uniformity is cheaper than that bug.
        """
        if peer_hall is not None:
            return self.trunk(peer_hall).ports
        return sum(t.ports for t in self.trunks)

    def used_ports(self, peer_hall: Optional[str] = None) -> int:
        return sum(
            a.ports for a in self.allocations.values()
            if peer_hall is None or a.peer_hall == peer_hall
        )

    def free_ports(self, peer_hall: Optional[str] = None) -> int:
        if peer_hall is None:
            return self.total_ports() - self.used_ports()
        return self.total_ports(peer_hall) - self.used_ports(peer_hall)

    # -- allocation ----------------------------------------------------------

    def allocate(self, request: Request) -> Allocation:
        """Take ports for a request, or raise :class:`RadixExhausted`."""
        if request.circuit_id in self.allocations:
            raise ValueError(f"circuit {request.circuit_id!r} is already allocated")
        trunk = self.trunk(request.peer_hall)
        needed = trunk.ports_for(request.bw_gbps)
        if needed > self.free_ports(request.peer_hall):
            raise RadixExhausted(
                f"{request.circuit_id!r} needs {needed} port(s) on the trunk to "
                f"{request.peer_hall}, which has {self.free_ports(request.peer_hall)} free "
                f"(the switch has {self.free_ports()} free in total, on other trunks)"
            )
        allocation = Allocation(
            request.circuit_id, request.peer_hall, needed, request.priority, request.job_id
        )
        self.allocations[request.circuit_id] = allocation
        return allocation

    def release(self, circuit_id: str) -> Allocation:
        if circuit_id not in self.allocations:
            raise KeyError(f"no allocation for circuit {circuit_id!r}")
        return self.allocations.pop(circuit_id)

    # -- the metric ----------------------------------------------------------

    def stranded_ports(self, demand: Sequence[Request]) -> int:
        """Free ports that cannot serve anything in this demand profile.

        A port is stranded when its trunk has no pending request, or when what
        remains free on its trunk is less than the smallest pending request for
        that trunk needs. The second case is the one that surprises people: a
        trunk with one free port and a pending 800G request on 400G optics has a
        stranded port, not spare capacity.
        """
        wanted: Dict[str, int] = {}
        for request in demand:
            try:
                trunk = self.trunk(request.peer_hall)
            except KeyError:
                continue
            need = trunk.ports_for(request.bw_gbps)
            wanted[request.peer_hall] = min(wanted.get(request.peer_hall, need), need)

        stranded = 0
        for trunk in self.trunks:
            free = self.free_ports(trunk.peer_hall)
            smallest = wanted.get(trunk.peer_hall)
            if smallest is None or free < smallest:
                stranded += free
        return stranded

    def fragmentation(self, demand: Sequence[Request]) -> float:
        """Stranded ports as a fraction of free ports. Zero when nothing is free.

        Quoted against a demand profile, never on its own. A switch with every
        port free and no demand is 100% fragmented by this definition and 0%
        fragmented by any useful one, which is why the demand argument is
        required rather than defaulted.
        """
        free = self.free_ports()
        if free == 0:
            return 0.0
        return self.stranded_ports(demand) / free


#: Above this many evictable circuits on one trunk, exact enumeration is
#: refused rather than silently taking exponential time. Twenty subsets is a
#: million; a real trunk carries far fewer distinct circuits than that.
MAX_EXACT_CANDIDATES = 20


@dataclass(frozen=True)
class Preemption:
    """A set of circuits to evict so a higher-priority request can be served."""

    victims: Tuple[Allocation, ...]
    ports_freed: int
    #: Sum of the caller-supplied cost of evicting each victim.
    cost: float

    @property
    def job_ids(self) -> Tuple[str, ...]:
        return tuple(sorted({v.job_id for v in self.victims if v.job_id}))


def preemption_plan(
    switch: OpticalSwitch,
    request: Request,
    cost_of_evicting,
) -> Optional[Preemption]:
    """The cheapest set of lower-priority circuits to evict for this request.

    ``cost_of_evicting(allocation) -> float`` is supplied by the caller, because
    what an eviction costs is not a property of the switch. The natural cost is
    what the victim's own retune would cost its job, which
    :mod:`ocintent.legality` computes --- so a preemption decision made here can
    be priced in the same accelerator-hours as everything else in the series.

    Exact rather than greedy. The candidate set is small (circuits on one trunk
    with strictly lower priority) and a greedy choice by cost-per-port gets the
    wrong answer whenever port counts differ, which they do as soon as two jobs
    ask for different widths. Enumerating subsets is affordable here and being
    approximately right about whose job gets killed is not a good trade.

    Returns ``None`` when no set of lower-priority circuits frees enough ports.
    """
    from itertools import combinations

    trunk = switch.trunk(request.peer_hall)
    needed = trunk.ports_for(request.bw_gbps) - switch.free_ports(request.peer_hall)
    if needed <= 0:
        return Preemption((), 0, 0.0)

    candidates = [
        a for a in switch.allocations.values()
        if a.peer_hall == request.peer_hall and a.priority < request.priority
    ]
    if len(candidates) > MAX_EXACT_CANDIDATES:
        raise ValueError(
            f"{len(candidates)} preemption candidates on the trunk to "
            f"{request.peer_hall}; exact enumeration is 2**n and this would not "
            f"return. Raise MAX_EXACT_CANDIDATES only with a plan for the runtime, "
            f"or partition the trunk first"
        )
    if sum(a.ports for a in candidates) < needed:
        return None

    best: Optional[Preemption] = None
    for size in range(1, len(candidates) + 1):
        for subset in combinations(candidates, size):
            freed = sum(a.ports for a in subset)
            if freed < needed:
                continue
            cost = sum(cost_of_evicting(a) for a in subset)
            if best is None or (cost, freed) < (best.cost, best.ports_freed):
                best = Preemption(tuple(subset), freed, cost)
    # No early exit on subset size. An earlier draft stopped at the first size
    # that produced any answer, reasoning that a larger subset can only cost
    # more --- which is false the moment costs differ per circuit. One wide
    # expensive victim frees the same ports as two narrow cheap ones, and the
    # early exit picked the expensive one every time. Enumeration is exact or
    # it is not worth its cost.
    return best
