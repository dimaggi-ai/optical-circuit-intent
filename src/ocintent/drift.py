"""The YAML says 800G at 8 microseconds. The path is 40 microseconds and dirty.

W5's campus bug. A declared circuit is an assertion someone typed; a measured
circuit is what the glass is doing today. This module hashes the first, compares
it against the second, and --- the part that makes it worth having --- predicts
when a path that is fine now will stop being fine.

Two pieces of the prediction rest on published physics rather than on anything
invented here, and they are the reason this repository has calibrated points at
all:

**Thermal delay.** Silica's refractive index and length both move with
temperature, and the combined effect on propagation delay is about 40
picoseconds per kilometre per kelvin. Over a 120 km metro path and a 25 K
diurnal swing at the duct, that is 120 nanoseconds of round-trip movement ---
which matters not because it is large but because it is *periodic*, and a
threshold set just above the cold-night value will alarm every afternoon.

**Bit error rate from margin.** Below a threshold receiver, the error rate is
``0.5 * erfc(Q / sqrt(2))`` where Q is the electrical signal-to-noise ratio.
This is the textbook relation, it is steep, and the steepness is the operational
point: a path does not degrade gracefully. Losing 2 dB of margin can move the
error rate by six orders of magnitude, so an insertion-loss trend that looks
gentle is the only warning available before the cliff.

What is *not* modelled: connector contamination, which is the single most common
cause of real insertion-loss faults and is an event rather than a trend. A
predictor that extrapolated a clean trend would say a path is healthy right up
until someone unmates a connector and mates it dirty. ASSUMPTIONS.md A5 says so;
the registry declines to claim otherwise.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

#: Change in one-way propagation delay with temperature, picoseconds per
#: kilometre per kelvin. Published figures for standard single-mode fibre
#: cluster around 37-40; 40 is used and SOURCES.md S2 records the range.
THERMAL_DELAY_PS_PER_KM_K = 40.0

#: Attenuation of G.652 fibre at 1550 nm, dB per km.
FIBRE_LOSS_DB_PER_KM = 0.20


def ber_from_q(q: float) -> float:
    """``0.5 * erfc(Q / sqrt(2))``. The textbook relation, unmodified.

    Written out rather than tabulated so the registry can check it against the
    published anchor pairs (Q = 6 gives about 1e-9; Q = 7 gives about 1.3e-12)
    rather than against a table this repository wrote.
    """
    if q <= 0:
        return 0.5
    return 0.5 * math.erfc(q / math.sqrt(2.0))


#: Decibels of optical margin per decade of Q, by the noise regime the
#: receiver runs in. ``direct``: a thermal-noise-limited direct-detection
#: receiver, whose photocurrent is proportional to optical power, so Q
#: moves a decade per 10 dB. ``coherent``: the OSNR-limited regime of an
#: amplified link, where Q squared follows the signal-to-noise ratio, so Q
#: moves a decade per 20 dB. The keys name the receivers each figure is
#: usually quoted for; the physics is the regime. The default is ``direct``
#: because the anchors in SOURCES.md S1 are written against
#: threshold-detected binary signalling; on the measured link in ``hedge``
#: every wavelength that failed was closer to ``coherent`` than to that
#: default, and steeper than both, and the registry says so.
DB_PER_DECADE_OF_Q: Dict[str, float] = {"direct": 10.0, "coherent": 20.0}

#: The error rate the forecast counts down to unless told otherwise: the
#: figure a threshold receiver is specified to. A coherent transponder runs
#: to its pre-FEC limit instead, near 3e-2 on the measured link
#: (``ocintent.hedge``), and a caller modelling one should pass that.
DEFAULT_TARGET_BER = 1e-12


def q_from_margin_db(
    margin_db: float, *, q_at_threshold: float = 6.0, detection: str = "direct",
) -> float:
    """Electrical Q from optical margin above the receiver threshold.

    One dB of optical power is two dB of electrical power for a direct-detection
    receiver, so Q scales as ``10 ** (margin_db / 10)`` in amplitude terms. The
    anchor is the receiver's rated threshold: at zero margin the receiver is at
    its specified operating point, conventionally Q = 6, which is the error rate
    a 1e-9 specification is written against. A coherent receiver halves the
    exponent (``detection="coherent"``); :data:`DB_PER_DECADE_OF_Q` says why.

    This is a first-order relation and it is the weakest link in the prediction
    chain. It ignores dispersion, nonlinearity, and every amplifier's noise
    contribution. Its job is to make the *shape* of the cliff right --- steep,
    and steeper the closer you are --- not to predict a particular receiver.
    """
    return q_at_threshold * (10.0 ** (margin_db / DB_PER_DECADE_OF_Q[detection]))


def ber_from_margin_db(
    margin_db: float, *, q_at_threshold: float = 6.0, detection: str = "direct",
) -> float:
    return ber_from_q(q_from_margin_db(
        margin_db, q_at_threshold=q_at_threshold, detection=detection,
    ))


def q_from_ber(ber: float) -> float:
    """The Q at which ``ber_from_q`` gives ``ber``. Bisection; no dependency.

    ``ber >= 0.5`` is Q = 0. A rate of zero has no finite Q and raises, because
    a caller who reaches this with zero errors has a counting window to
    report, not an error rate.
    """
    if ber <= 0.0:
        raise ValueError("an error rate of zero has no finite Q")
    if ber >= 0.5:
        return 0.0
    lo, hi = 0.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if ber_from_q(mid) > ber:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def margin_span_db(ber_from: float, ber_to: float, *, detection: str = "direct") -> float:
    """Decibels of margin between two error rates under one scaling.

    Positive when ``ber_to`` is the worse rate. Independent of the threshold
    anchor: only the ratio of the two Qs and the dB-per-decade enter.
    """
    q_from, q_to = q_from_ber(ber_from), q_from_ber(ber_to)
    if q_to <= 0.0 or q_from <= 0.0:
        return math.inf
    return DB_PER_DECADE_OF_Q[detection] * math.log10(q_from / q_to)


@dataclass(frozen=True)
class DeclaredCircuit:
    """What the inventory says a circuit is."""

    circuit_id: str
    a_hall: str
    z_hall: str
    bw_gbps: float
    rtt_us: float
    path_km: float
    il_db: float
    connectors: int = 4

    def __post_init__(self) -> None:
        if not self.circuit_id:
            raise ValueError("a circuit needs an id")
        if self.a_hall == self.z_hall:
            raise ValueError("a circuit's two halls must differ")
        for name in ("bw_gbps", "rtt_us", "path_km", "il_db"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.connectors < 0:
            raise ValueError("connectors must be non-negative")

    def canonical(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def topology_hash(self) -> str:
        """The hash a compile cache and a span envelope key against."""
        return hashlib.sha256(self.canonical().encode()).hexdigest()


@dataclass(frozen=True)
class MeasuredCircuit:
    """What the plant reports about the same circuit today."""

    circuit_id: str
    rtt_us: float
    il_db: float
    ber: float
    bw_gbps: float
    #: Duct temperature at the time of measurement, if known.
    temperature_c: Optional[float] = None
    age_s: float = 0.0


class DriftVerdict(str, Enum):
    MATCHES = "matches"
    DRIFTED = "drifted"
    #: The measured circuit is not the declared one at all.
    WRONG_CIRCUIT = "wrong-circuit"


@dataclass(frozen=True)
class FieldDrift:
    field: str
    declared: float
    measured: float
    tolerance: float

    @property
    def absolute(self) -> float:
        return self.measured - self.declared

    @property
    def relative(self) -> float:
        if self.declared == 0:
            return math.inf if self.measured else 0.0
        return (self.measured - self.declared) / self.declared

    @property
    def exceeded(self) -> bool:
        return abs(self.relative) > self.tolerance

    def __str__(self) -> str:
        return (
            f"{self.field}: declared {self.declared:g}, measured {self.measured:g} "
            f"({self.relative * 100:+.1f}%, tolerance {self.tolerance * 100:.0f}%)"
        )


@dataclass(frozen=True)
class DriftReport:
    circuit_id: str
    verdict: DriftVerdict
    fields: Tuple[FieldDrift, ...]
    topology_hash: str
    note: str = ""

    @property
    def exceeded(self) -> Tuple[FieldDrift, ...]:
        return tuple(f for f in self.fields if f.exceeded)

    def explain(self) -> str:
        if self.verdict is DriftVerdict.WRONG_CIRCUIT:
            return f"{self.circuit_id}: {self.note}"
        if self.verdict is DriftVerdict.MATCHES:
            return f"{self.circuit_id}: measured path matches the declared one"
        return f"{self.circuit_id}: " + "; ".join(str(f) for f in self.exceeded)


#: Default tolerances, as fractions. Round-trip time is tightest because it is
#: the field a compiler plans against; bandwidth is loosest because a circuit
#: rarely delivers its nameplate and everyone knows it. ASSUMPTIONS.md A3.
DEFAULT_TOLERANCES: Dict[str, float] = {
    "rtt_us": 0.10,
    "il_db": 0.20,
    "bw_gbps": 0.25,
}


def compare(
    declared: DeclaredCircuit,
    measured: MeasuredCircuit,
    tolerances: Optional[Dict[str, float]] = None,
) -> DriftReport:
    """Hash the declared circuit and check the measured one against it."""
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    topology = declared.topology_hash()

    if declared.circuit_id != measured.circuit_id:
        return DriftReport(
            declared.circuit_id, DriftVerdict.WRONG_CIRCUIT, (), topology,
            note=(
                f"measurement is for {measured.circuit_id!r}; the inventory and the "
                "plant do not agree on which circuit this is, which makes every other "
                "comparison meaningless"
            ),
        )

    fields = (
        FieldDrift("rtt_us", declared.rtt_us, measured.rtt_us, tol["rtt_us"]),
        FieldDrift("il_db", declared.il_db, measured.il_db, tol["il_db"]),
        FieldDrift("bw_gbps", declared.bw_gbps, measured.bw_gbps, tol["bw_gbps"]),
    )
    verdict = DriftVerdict.DRIFTED if any(f.exceeded for f in fields) else DriftVerdict.MATCHES
    return DriftReport(declared.circuit_id, verdict, fields, topology)


# --------------------------------------------------------------------------
# prediction
# --------------------------------------------------------------------------


def thermal_rtt_swing_us(path_km: float, delta_t_k: float) -> float:
    """Round-trip delay movement over a temperature swing, microseconds.

    Round trip, so twice the one-way figure. Included because a monitoring
    threshold set without it will alarm on the weather.
    """
    one_way_ps = THERMAL_DELAY_PS_PER_KM_K * path_km * delta_t_k
    return 2.0 * one_way_ps * 1e-6


@dataclass(frozen=True)
class DriftForecast:
    """Where a path is heading, and when it stops being usable."""

    circuit_id: str
    il_now_db: float
    il_rate_db_per_year: float
    margin_now_db: float
    ber_now: float
    #: Years until the error rate crosses the target. ``inf`` if not within
    #: ``horizon_years``.
    years_to_threshold: float
    target_ber: float
    horizon_years: float = 25.0

    @property
    def actionable(self) -> bool:
        """Whether the crossing is near enough to plan a maintenance window for."""
        return self.years_to_threshold < 1.0

    def explain(self) -> str:
        if math.isinf(self.years_to_threshold) and self.il_rate_db_per_year <= 0:
            return (
                f"{self.circuit_id}: {self.margin_now_db:.2f} dB of margin and no "
                f"measured loss trend; nothing to forecast"
            )
        if math.isinf(self.years_to_threshold):
            return (
                f"{self.circuit_id}: {self.il_now_db:.2f} dB now, rising "
                f"{self.il_rate_db_per_year:.3f} dB/year, {self.margin_now_db:.2f} dB of "
                f"margin left; does not cross a {self.target_ber:.0e} error rate within "
                f"{self.horizon_years:g} years"
            )
        return (
            f"{self.circuit_id}: {self.il_now_db:.2f} dB now, rising "
            f"{self.il_rate_db_per_year:.3f} dB/year, {self.margin_now_db:.2f} dB of "
            f"margin left; crosses a {self.target_ber:.0e} error rate in "
            f"{self.years_to_threshold:.2f} years"
        )


def forecast(
    circuit_id: str,
    il_now_db: float,
    il_rate_db_per_year: float,
    receiver_budget_db: float,
    *,
    target_ber: float = DEFAULT_TARGET_BER,
    q_at_threshold: float = 6.0,
    horizon_years: float = 25.0,
    detection: str = "direct",
) -> DriftForecast:
    """When will a slowly-worsening path cross an error-rate target?

    Solved by bisection on the margin, not by inverting ``erfc``. The inverse
    exists but is not in the standard library, and bringing in a dependency to
    avoid ten lines of bisection on a monotone function would be the wrong
    trade for a repository whose only dependency is the standard library.

    ``horizon_years`` bounds the answer. A path crossing in forty years is not a
    forecast, it is a rounding error on the loss rate, and reporting it as a
    date invites someone to act on it.

    ``target_ber`` defaults to a threshold-receiver figure. A coherent
    transponder with forward error correction fails at its *pre-FEC* limit,
    which the measured link in ``hedge`` puts near 3e-2; pass that as the
    target, and ``detection="coherent"``, for such a plant.
    """
    if il_rate_db_per_year <= 0:
        margin = receiver_budget_db - il_now_db
        return DriftForecast(
            circuit_id, il_now_db, il_rate_db_per_year, margin,
            ber_from_margin_db(margin, q_at_threshold=q_at_threshold, detection=detection),
            math.inf, target_ber, horizon_years,
        )

    margin_now = receiver_budget_db - il_now_db
    ber_now = ber_from_margin_db(margin_now, q_at_threshold=q_at_threshold, detection=detection)

    def ber_at(years: float) -> float:
        margin = receiver_budget_db - (il_now_db + il_rate_db_per_year * years)
        return ber_from_margin_db(margin, q_at_threshold=q_at_threshold, detection=detection)

    if ber_now >= target_ber:
        years = 0.0
    elif ber_at(horizon_years) < target_ber:
        years = math.inf
    else:
        lo, hi = 0.0, horizon_years
        for _ in range(200):
            mid = (lo + hi) / 2.0
            if ber_at(mid) < target_ber:
                lo = mid
            else:
                hi = mid
        years = (lo + hi) / 2.0

    return DriftForecast(
        circuit_id, il_now_db, il_rate_db_per_year, margin_now, ber_now, years,
        target_ber, horizon_years,
    )
