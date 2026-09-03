"""What to ask an optical plant for, when it is legal to ask, and what it costs.

Six models, each usable on its own:

``intent``
    A verb, two endpoints, a bandwidth, a boundary. Compiles to a plan of
    generic operations. No vendor session is opened; plans are returned, not
    executed (DECISIONS.md D2).
``legality``
    When a retune is invisible, when it stalls the job, and when it kills it.
    Two defensible objectives that disagree over a wide band of retune times.
``radix``
    Ports are not interchangeable across trunks. Free ports that cannot serve
    pending demand are stranded, and stranded ports are the fragmentation
    number that matters.
``checkpoint``
    The stop tax you can see in a training curve, and the contention tax you
    cannot see unless you measure it.
``drift``
    The declared circuit against the measured one, and a forecast of when a
    path that is fine now stops being fine.
``ledger``
    All of the above, aged and totalled, in the shape finance already reads.
``hedge``
    The one measured link: a laboratory testbed that was bent and attenuated
    on purpose while its transponders and a Layer-3 probe were polled. Read
    from the raw files, which are fetched and SHA-pinned rather than vendored.
``adapters``
    The generic plan compiled to one documented controller interface and
    handed back, never sent. One binding ships: the TAPI 2.1.5 RESTCONF data
    tree under the TR-547 v1.2 agreement (``adapters.tapi``).

``drift.compare`` and ``checkpoint.compare`` are both named ``compare`` in
their own modules, which is right there and wrong here, so this namespace
exports them as :func:`compare_circuit` and :func:`compare_strategies`.
"""

from __future__ import annotations

from . import adapters, checkpoint, drift, hedge, intent, ledger, legality, radix
from .checkpoint import (
    CheckpointPlan,
    StallCause,
    StallEvidence,
    Strategy,
    Tax,
    cheapest_durable,
    classify_stall,
)
from .checkpoint import compare as compare_strategies
from .checkpoint import tax
from .drift import (
    DB_PER_DECADE_OF_Q,
    DEFAULT_TARGET_BER,
    DeclaredCircuit,
    DriftForecast,
    DriftReport,
    DriftVerdict,
    MeasuredCircuit,
    THERMAL_DELAY_PS_PER_KM_K,
    ber_from_margin_db,
    ber_from_q,
    forecast,
    margin_span_db,
    q_from_ber,
    q_from_margin_db,
    thermal_rtt_swing_us,
)
from .hedge import HEDGE_COMMIT, HedgeDataError, RunSummary, WavelengthTimeline
from .drift import compare as compare_circuit
from .intent import Boundary, Endpoint, Intent, Operation, Plan, Verb, compile_intent
from .ledger import AGING_BUCKETS, Cause, DebtEntry, Ledger, debt_from_drift, debt_from_stranded_ports
from .legality import (
    JobRhythm,
    Legality,
    RetuneCost,
    assess,
    cheapest_legal,
    disagreement_intervals,
    disagreement_width_s,
    ladder,
    objectives_disagree,
    soonest_legal,
)
from .radix import (
    Allocation,
    OpticalSwitch,
    Preemption,
    RadixExhausted,
    Request,
    Trunk,
    preemption_plan,
)

__version__ = "1.2.0"

__all__ = [
    "__version__",
    # modules
    "adapters", "checkpoint", "drift", "hedge", "intent", "ledger", "legality", "radix",
    # intent
    "Boundary", "Endpoint", "Intent", "Operation", "Plan", "Verb", "compile_intent",
    # legality
    "JobRhythm", "Legality", "RetuneCost", "assess", "cheapest_legal", "ladder",
    "soonest_legal", "objectives_disagree", "disagreement_intervals",
    "disagreement_width_s",
    # radix
    "Allocation", "OpticalSwitch", "Preemption", "RadixExhausted", "Request", "Trunk",
    "preemption_plan",
    # checkpoint
    "CheckpointPlan", "Strategy", "Tax", "tax", "compare_strategies",
    "cheapest_durable", "StallCause", "StallEvidence", "classify_stall",
    # drift
    "DeclaredCircuit", "MeasuredCircuit", "DriftReport", "DriftVerdict",
    "DriftForecast", "compare_circuit", "forecast", "ber_from_q", "q_from_ber",
    "q_from_margin_db", "ber_from_margin_db", "margin_span_db",
    "thermal_rtt_swing_us", "THERMAL_DELAY_PS_PER_KM_K", "DB_PER_DECADE_OF_Q",
    "DEFAULT_TARGET_BER",
    # hedge
    "HEDGE_COMMIT", "HedgeDataError", "RunSummary", "WavelengthTimeline",
    # ledger
    "Cause", "DebtEntry", "Ledger", "AGING_BUCKETS", "debt_from_drift",
    "debt_from_stranded_ports",
]
