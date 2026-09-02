"""Debt is the product. The ledger is what finance reads.

A drifted circuit, a trunk full of stranded ports, a retune that has to wait
for an epoch boundary --- each of these is capacity the plant owner paid for
and is not getting. Individually they are engineering tickets. Accumulated,
aged, and totalled they are a balance, and a balance is the only form in which
this information survives contact with the people who approve maintenance
windows and capital.

So the ledger is deliberately shaped like an accounts-receivable aging
schedule: entries open, accrue while they stay open, age into buckets, and
close when someone fixes the thing. The shape is borrowed on purpose. A
capacity report in a new format gets read once; an aging schedule gets read
every month by people who already know how to read one.

Two decisions worth stating plainly, because both cost something:

**The unit is accelerator-hours, not currency.** Converting requires a rate ---
what an accelerator-hour is worth to *this* owner, which depends on their
contract, their utilisation, and whether the alternative use of that hour
exists. The plant owner has that number. This repository does not, and a rate
invented here would travel downstream looking like a measurement. DECISIONS.md
D6. ``Ledger.priced()`` exists for an owner who supplies their own rate; it
takes the rate as an argument and never defaults it.

**Debt accrues while it stays open.** An entry carries a one-off cost and a
daily rate, and outstanding debt is the sum. This is the whole argument for
remediation: a 40-microsecond path that should be 8 does not cost a fixed
amount, it costs an amount per day, and the maintenance window that fixes it
has a payback period you can compute. ``payback_days()`` computes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SECONDS_PER_DAY = 86_400.0


class Cause(str, Enum):
    """Why capacity is not being delivered.

    Each maps to a mechanism modelled elsewhere in this package, so an entry
    can always be traced back to something measured rather than asserted.
    """

    #: Measured path is worse than the declared one (:mod:`ocintent.drift`).
    DRIFT = "drift"
    #: Free ports that cannot serve pending demand (:mod:`ocintent.radix`).
    STRANDED_PORTS = "stranded-ports"
    #: A retune waiting for a legal boundary (:mod:`ocintent.legality`).
    RETUNE_STALL = "retune-stall"
    #: Checkpoint traffic competing with a collective (:mod:`ocintent.checkpoint`).
    CHECKPOINT_CONTENTION = "checkpoint-contention"
    #: A span the contract refused, running local and smaller instead.
    REFUSED_SPAN = "refused-span"
    #: The circuit the plant reports is not the circuit the inventory declares.
    INVENTORY_ERROR = "inventory-error"


@dataclass
class DebtEntry:
    """One thing that is costing capacity."""

    entry_id: str
    hall: str
    cause: Cause
    opened_at_s: float
    #: Capacity already lost when the entry was opened.
    upfront_accelerator_hours: float = 0.0
    #: Capacity lost per day for as long as it stays open.
    daily_accelerator_hours: float = 0.0
    #: Accelerator-hours the fix itself costs (the window, the drain, the refill).
    remediation_accelerator_hours: float = 0.0
    circuit_id: Optional[str] = None
    note: str = ""
    closed_at_s: Optional[float] = None

    def __post_init__(self) -> None:
        if self.upfront_accelerator_hours < 0 or self.daily_accelerator_hours < 0:
            raise ValueError("debt cannot be negative")
        if self.remediation_accelerator_hours < 0:
            raise ValueError("remediation cannot be negative")
        if self.closed_at_s is not None and self.closed_at_s < self.opened_at_s:
            raise ValueError("an entry cannot close before it opens")

    @property
    def open(self) -> bool:
        return self.closed_at_s is None

    def age_days(self, now_s: float) -> float:
        """Days the entry has been open, stopping at the close."""
        end = self.closed_at_s if self.closed_at_s is not None else now_s
        return max(0.0, (end - self.opened_at_s) / SECONDS_PER_DAY)

    def accrued(self, now_s: float) -> float:
        """Total accelerator-hours this entry has cost by ``now_s``."""
        return self.upfront_accelerator_hours + self.daily_accelerator_hours * self.age_days(now_s)

    def payback_days(self) -> float:
        """Days for the fix to pay for itself, or ``inf`` if it never does.

        An entry that costs nothing per day is not worth a window on economic
        grounds alone --- it may still be worth one on risk grounds, which this
        number does not capture and does not claim to.
        """
        if self.daily_accelerator_hours <= 0:
            return float("inf")
        return self.remediation_accelerator_hours / self.daily_accelerator_hours


#: Aging buckets in days, upper-exclusive; the last is open-ended.
AGING_BUCKETS: Tuple[Tuple[str, float, float], ...] = (
    ("0-7d", 0.0, 7.0),
    ("7-30d", 7.0, 30.0),
    ("30-90d", 30.0, 90.0),
    ("90d+", 90.0, float("inf")),
)


@dataclass
class Ledger:
    entries: List[DebtEntry] = field(default_factory=list)

    def open_entry(self, entry: DebtEntry) -> DebtEntry:
        if any(e.entry_id == entry.entry_id for e in self.entries):
            raise ValueError(f"duplicate entry id {entry.entry_id!r}")
        self.entries.append(entry)
        return entry

    def close_entry(self, entry_id: str, at_s: float) -> DebtEntry:
        for e in self.entries:
            if e.entry_id == entry_id:
                if not e.open:
                    raise ValueError(f"{entry_id!r} is already closed")
                if at_s < e.opened_at_s:
                    raise ValueError("an entry cannot close before it opens")
                e.closed_at_s = at_s
                return e
        raise KeyError(entry_id)

    def outstanding(self) -> Tuple[DebtEntry, ...]:
        return tuple(e for e in self.entries if e.open)

    def total(self, now_s: float, *, open_only: bool = True) -> float:
        rows = self.outstanding() if open_only else tuple(self.entries)
        return sum(e.accrued(now_s) for e in rows)

    def by_hall(self, now_s: float, *, open_only: bool = True) -> Dict[str, float]:
        rows = self.outstanding() if open_only else tuple(self.entries)
        out: Dict[str, float] = {}
        for e in rows:
            out[e.hall] = out.get(e.hall, 0.0) + e.accrued(now_s)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def by_cause(self, now_s: float, *, open_only: bool = True) -> Dict[Cause, float]:
        rows = self.outstanding() if open_only else tuple(self.entries)
        out: Dict[Cause, float] = {}
        for e in rows:
            out[e.cause] = out.get(e.cause, 0.0) + e.accrued(now_s)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def aging(self, now_s: float) -> Dict[str, float]:
        """Outstanding debt by how long it has been outstanding.

        Only open entries. A closed entry has no age --- it has a history, and
        history belongs in :meth:`total` with ``open_only=False``.
        """
        out = {label: 0.0 for label, _, _ in AGING_BUCKETS}
        for e in self.outstanding():
            age = e.age_days(now_s)
            for label, lo, hi in AGING_BUCKETS:
                if lo <= age < hi:
                    out[label] += e.accrued(now_s)
                    break
        return out

    def worst_first(self, now_s: float, limit: int = 10) -> Tuple[DebtEntry, ...]:
        """Open entries by daily rate, which is the order to fix them in.

        By rate, not by accrued total: a large old entry that has stopped
        costing anything is a worse use of a maintenance window than a small
        new one that is still bleeding.
        """
        rows = sorted(self.outstanding(), key=lambda e: -e.daily_accelerator_hours)
        return tuple(rows[:limit])

    def priced(self, now_s: float, currency_per_accelerator_hour: float) -> float:
        """Total in the owner's currency, at a rate the owner supplies.

        There is no default. See the module docstring and DECISIONS.md D6.
        """
        if currency_per_accelerator_hour < 0:
            raise ValueError("a rate cannot be negative")
        return self.total(now_s) * currency_per_accelerator_hour

    # ---------------------------------------------------------------- report

    def report(self, now_s: float) -> str:
        """The page finance reads."""
        lines: List[str] = []
        total = self.total(now_s)
        lines.append(f"outstanding capacity debt: {total:,.0f} accelerator-hours")
        lines.append(f"open entries: {len(self.outstanding())} of {len(self.entries)}")
        lines.append("")
        lines.append("by age")
        for label, amount in self.aging(now_s).items():
            share = (amount / total * 100.0) if total else 0.0
            lines.append(f"  {label:<8} {amount:>12,.0f}  {share:5.1f}%")
        lines.append("")
        lines.append("by cause")
        for cause, amount in self.by_cause(now_s).items():
            lines.append(f"  {cause.value:<24} {amount:>12,.0f}")
        lines.append("")
        lines.append("by hall")
        for hall, amount in self.by_hall(now_s).items():
            lines.append(f"  {hall:<24} {amount:>12,.0f}")
        worst = self.worst_first(now_s, limit=5)
        if worst:
            lines.append("")
            lines.append("fix these first (by daily rate, with payback)")
            for e in worst:
                pb = e.payback_days()
                pb_s = "never pays back" if pb == float("inf") else f"pays back in {pb:.1f} d"
                lines.append(
                    f"  {e.entry_id:<20} {e.daily_accelerator_hours:>8,.0f}/day  "
                    f"{e.age_days(now_s):>5.0f} d old  {pb_s}"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------ i/o

    def to_json(self) -> str:
        rows = []
        for e in self.entries:
            row = {
                "entry_id": e.entry_id,
                "hall": e.hall,
                "cause": e.cause.value,
                "opened_at_s": e.opened_at_s,
                "upfront_accelerator_hours": e.upfront_accelerator_hours,
                "daily_accelerator_hours": e.daily_accelerator_hours,
                "remediation_accelerator_hours": e.remediation_accelerator_hours,
                "circuit_id": e.circuit_id,
                "note": e.note,
                "closed_at_s": e.closed_at_s,
            }
            rows.append(row)
        return json.dumps({"entries": rows}, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Ledger":
        data = json.loads(text)
        entries = []
        for row in data.get("entries", []):
            row = dict(row)
            row["cause"] = Cause(row["cause"])
            entries.append(DebtEntry(**row))
        return cls(entries)


# --------------------------------------------------------------------------
# bridges from the other modules
# --------------------------------------------------------------------------


def debt_from_drift(
    report: "object",
    *,
    hall: str,
    opened_at_s: float,
    accelerators: int,
    slowdown: float,
    remediation_accelerator_hours: float = 0.0,
    entry_id: Optional[str] = None,
) -> DebtEntry:
    """Turn a :class:`ocintent.drift.DriftReport` into a ledger entry.

    ``slowdown`` is the fraction of the job's wall-clock the drifted path adds
    --- it is an input, not something the drift report knows, because the cost
    of a slow circuit depends entirely on how much the job uses it. A 5x
    round-trip on a path a job touches once per epoch is nearly free; the same
    path under a tensor-parallel collective is fatal. The caller has the
    communication profile. This function refuses to guess it.
    """
    from .drift import DriftReport, DriftVerdict  # local import: avoids a cycle

    if not isinstance(report, DriftReport):
        raise TypeError("expected a DriftReport")
    if not 0.0 <= slowdown:
        raise ValueError("slowdown must be non-negative")
    cause = (
        Cause.INVENTORY_ERROR
        if report.verdict is DriftVerdict.WRONG_CIRCUIT
        else Cause.DRIFT
    )
    daily = accelerators * 24.0 * slowdown
    return DebtEntry(
        entry_id=entry_id or f"drift-{report.circuit_id}",
        hall=hall,
        cause=cause,
        opened_at_s=opened_at_s,
        daily_accelerator_hours=daily,
        remediation_accelerator_hours=remediation_accelerator_hours,
        circuit_id=report.circuit_id,
        note=report.explain(),
    )


def debt_from_stranded_ports(
    hall: str,
    stranded: int,
    accelerators_per_port: int,
    *,
    opened_at_s: float,
    remediation_accelerator_hours: float = 0.0,
    entry_id: Optional[str] = None,
) -> DebtEntry:
    """Stranded optical ports as a daily accrual.

    The conversion is one multiplication and it is the caller's number: how
    many accelerators sit behind a port is a property of the plant's build, not
    of the switch.
    """
    if stranded < 0 or accelerators_per_port < 0:
        raise ValueError("counts must be non-negative")
    return DebtEntry(
        entry_id=entry_id or f"stranded-{hall}",
        hall=hall,
        cause=Cause.STRANDED_PORTS,
        opened_at_s=opened_at_s,
        daily_accelerator_hours=stranded * accelerators_per_port * 24.0,
        remediation_accelerator_hours=remediation_accelerator_hours,
        note=f"{stranded} free ports cannot serve pending demand",
    )
