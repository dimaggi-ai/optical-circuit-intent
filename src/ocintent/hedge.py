"""A measured link, read from its raw files: the HEDGE testbed.

Every other module in this package is a model. This one is a measurement.
Devraj et al. (NSDI '26) built an inter-datacenter link --- more than 100 km of
fibre, four cascaded amplifiers, a ROADM, and transponders --- and broke it on
purpose three times while polling the transponders' bit error rate,
uncorrectable-FEC counter and received power about every 1.6 seconds, with
four UDP iperf sessions running across the link to say whether Layer 3 was
still carrying traffic. (That the transponders are coherent and the error
rate is the pre-FEC one is this repository's reading: the paper reports a bit
error rate under a 25% FEC overhead and uses neither word.) They published
the raw files. This module reads them and answers, per wavelength, the
questions the drift model could only pose:

* when the pre-FEC error rate left its starting level (``onset``),
* when the uncorrectable-FEC counter first moved (``failed``, the authors'
  own failure marker),
* when the counter stopped moving again (``restabilised``, the authors'
  recovery marker),
* how much attenuation the wavelength survived before the cliff, and
* how long after the *first* wavelength failed the link itself went dark.

Three runs. ``wdl`` bent the fibre around a 3/4-inch cylinder with three
wavelengths spread across the C-band, all at 16-QAM (Figure 4a/4b).
``mod_formats`` bent it again with the three wavelengths at PM-QPSK, 8-QAM and
16-QAM (Figure 4c). ``prototype`` wound a variable optical attenuator into the
transmit side of a four-wavelength link (Figure 14b/14c). The channel labels
and the analysis windows come from the authors' published notebooks, not from
anything chosen here; DECISIONS.md D14 says which and why.

What this is not. It is a laboratory link with two induced failure modes, a
UDP availability probe rather than a collective, and no documented clock
synchronisation between the transponder poller and the iperf hosts. Nothing
measured here transfers to another plant as a constant. The registry's
declined list carries the full wording; the useful part is that the *shape*
of the answer --- the first alarm leads the outage by the spread of the
wavelengths' failure times, and the outage begins inside the poll interval
in which the last wavelength fails --- is now measured rather than assumed.

One thing about the iperf logs has to be known before they can be read. Each
line's ``<epoch ms>|`` prefix was stamped when iperf's block-buffered stdout
was flushed, not when the interval ended: lines arrive in bursts, and a
stamp trails the interval it describes by up to five seconds. Every report
is therefore re-timed from iperf's own ``a.aa-b.bb sec`` field, anchored per
log at the least-late line (DECISIONS.md D14). :class:`Retiming` carries
what was measured about each log's stamps, and the registry prints it.

The files are fetched, not vendored: the upstream repository carries no
license grant, so redistribution is not this repository's call to make.
``make data`` pulls the fifteen files from the pinned commit and refuses any
whose SHA-256 does not match :data:`MANIFEST`. A missing or altered file
raises :class:`HedgeDataError`, and every registry point that reads the data
turns red rather than skipping.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .drift import DB_PER_DECADE_OF_Q, margin_span_db

__all__ = [
    "HEDGE_REPO",
    "HEDGE_COMMIT",
    "HEDGE_RAW_BASE",
    "RUNS",
    "FILES",
    "MANIFEST",
    "LABELS",
    "WINDOWS",
    "DISTURBANCE",
    "FIGURES",
    "HedgeDataError",
    "Sample",
    "Wavelength",
    "Report",
    "Retiming",
    "Run",
    "WavelengthTimeline",
    "ScalingCheck",
    "RunSummary",
    "Problem",
    "verify",
    "load_run",
    "summarize",
    "load_summaries",
    "first_counter_change",
    "counter_restabilised",
    "link_down_intervals",
    "scaling_check",
    "report",
    "timeline",
    "sha256_of",
    "REPORT_INTERVAL_S",
]

HEDGE_REPO = "https://github.com/hedge-wan/hedge"
#: The commit every file is pinned to. Fetched 2026-09-02.
HEDGE_COMMIT = "9c6540cf042a4933e918f9b306fcf116d8776e2f"
HEDGE_RAW_BASE = (
    f"https://raw.githubusercontent.com/hedge-wan/hedge/{HEDGE_COMMIT}/"
    "hardware-experiments/data"
)

RUNS: Tuple[str, ...] = ("prototype", "wdl", "mod_formats")
FILES: Tuple[str, ...] = (
    "transponder_data.csv", "server1.log", "server2.log", "server3.log", "server4.log",
)

#: SHA-256 of every file as fetched from :data:`HEDGE_COMMIT`. ``make data``
#: verifies these; :func:`load_run` verifies them again before reading.
MANIFEST: Dict[str, str] = {
    "prototype/transponder_data.csv":
        "0a4d8d8148847138be071318b90a642c0870911f931c22f8bbd4a754c8a47784",
    "prototype/server1.log":
        "47c8b5ed9340a596bdf91c254ace242c1f54c387b741898803267c9dbf8fe769",
    "prototype/server2.log":
        "40552b815b24e428c9b712193dce5e7992873ec60fc2a46f8a7e2be674cce5af",
    "prototype/server3.log":
        "eff92dc6d4d5f4bd00ae9029c79fd0434b68beaf3b57a361b443727e9de9d48d",
    "prototype/server4.log":
        "584a463cdd3bb230a5ff9968f1850679a292ae56d1e5591109d78a525f938e37",
    "wdl/transponder_data.csv":
        "b60df6cd51844791efe600353a258c960bab8d7a35c8aada3b060649a8236ac4",
    "wdl/server1.log":
        "d83924c0a720f098169d7cd205c3107f1bd0c95e3f48ddf46fcfb53f5fb17904",
    "wdl/server2.log":
        "632461ebf7046a21bcccfc2942b034fb88c4b3d83ffbb4cf57031b8b791c9ec6",
    "wdl/server3.log":
        "b6c67594c29219cecc30839496cb15ade0fd10158dcb604fae5af1315da5fed9",
    "wdl/server4.log":
        "40b1f1079f6115667817ce21ca39546d5d0e1eb29d9dd77e54d511938d361921",
    "mod_formats/transponder_data.csv":
        "ee49495038c50203f42850f4e0e452fcb868e6e1857e8fd6336ed2a5e805f1cb",
    "mod_formats/server1.log":
        "fc8b644c03791c8b4718eb98abdf33147fcc3bab8d7073aaeeed0a70e0c86dfb",
    "mod_formats/server2.log":
        "cbcdc5f754e0c7266b2904e5f5e32e8651cf75da6b9128e0fec5b327958992f7",
    "mod_formats/server3.log":
        "b55bfb67367eaa7939d746d7e3505a4f66193586be455177b5fea31eebacc416",
    "mod_formats/server4.log":
        "6dd44f81ed5584effd476224fb6a9633997a8d5191dc44c0b24f695d1dcb2139",
}

#: Channel id to label, exactly as the authors' notebooks map them. The
#: ``wdl`` file carries a fourth channel (84) that the paper's Figure 4a/4b
#: does not plot; it is read and reported but carries no label and takes no
#: part in any ordering claim.
LABELS: Dict[str, Dict[int, str]] = {
    "prototype": {28: "16-QAM", 52: "8-QAM", 74: "8-QAM", 84: "PM-QPSK"},
    "mod_formats": {3: "16-QAM", 52: "8-QAM", 74: "8-QAM", 84: "PM-QPSK"},
    "wdl": {3: "1567 nm", 52: "1547 nm", 74: "1539 nm"},
}

#: Analysis windows in seconds after the first transponder sample, taken from
#: the ``xlim`` in each notebook. The bend notebooks' comments say an
#: unrelated attenuator experiment follows the window; the prototype
#: notebook calls 250-650 s "the relevant time period with VOA tuning", so
#: the attenuator sweep begins inside its window, not before it.
WINDOWS: Dict[str, Tuple[float, float]] = {
    "prototype": (250.0, 650.0),
    "wdl": (0.0, 350.0),
    "mod_formats": (0.0, 300.0),
}

DISTURBANCE: Dict[str, str] = {
    "prototype": "variable optical attenuator on the transmit side",
    "wdl": "3/4-inch macrobend, three wavelengths across the C-band at 16-QAM",
    "mod_formats": "3/4-inch macrobend, three modulation formats",
}

FIGURES: Dict[str, str] = {
    "prototype": "Figure 14b/14c, Appendix A.5",
    "wdl": "Figure 4a/4b, Section 3.1 Finding 1",
    "mod_formats": "Figure 4c, Section 3.1 Finding 2",
}

#: How often iperf reported, in seconds. The value of ``-i`` the logs show.
REPORT_INTERVAL_S = 1.0


class HedgeDataError(RuntimeError):
    """A pinned file is missing or does not match its SHA-256.

    Raised rather than skipped, so a registry run without the data is red.
    """


# --------------------------------------------------------------------------
# raw records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """One transponder poll for one wavelength. ``t_s`` is relative to the run."""

    t_s: float
    ber: float
    fec: float
    power_dbm: float


@dataclass(frozen=True)
class Wavelength:
    channel: int
    label: Optional[str]
    samples: Tuple[Sample, ...]


@dataclass(frozen=True)
class Report:
    """One iperf ``[SUM]`` interval on one server.

    ``start_s`` and ``t_s`` are the interval's start and end on the run's
    clock, re-timed from iperf's own interval field rather than read from the
    line's stamp (DECISIONS.md D14). ``gbps`` is the received rate over it.
    """

    start_s: float
    t_s: float
    gbps: float


@dataclass(frozen=True)
class Retiming:
    """What re-timing one server log measured about its stamps.

    ``anchor_s`` is where iperf's interval clock sat on the run's clock: the
    least-late line's stamp minus its interval end. ``max_late_s`` is the
    most any line's stamp trailed its interval end after anchoring, and
    ``late_lines`` counts the lines that trailed by more than one report
    interval, out of ``lines``.
    """

    anchor_s: float
    max_late_s: float
    late_lines: int
    lines: int


@dataclass(frozen=True)
class Run:
    name: str
    t0_epoch_s: float
    window: Tuple[float, float]
    wavelengths: Tuple[Wavelength, ...]
    servers: Tuple[Tuple[Report, ...], ...]
    retiming: Tuple[Retiming, ...]

    def wavelength(self, channel: int) -> Wavelength:
        for w in self.wavelengths:
            if w.channel == channel:
                return w
        raise KeyError(channel)


@dataclass(frozen=True)
class Problem:
    path: str
    what: str  # "missing" or "sha256 mismatch"


# --------------------------------------------------------------------------
# fetch-side verification
# --------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(data_dir: Path, run: Optional[str] = None) -> Tuple[Problem, ...]:
    """Every pinned file present and matching, or the list of what is wrong."""
    problems: List[Problem] = []
    for rel, want in MANIFEST.items():
        if run is not None and not rel.startswith(run + "/"):
            continue
        path = Path(data_dir) / rel
        if not path.is_file():
            problems.append(Problem(rel, "missing"))
        elif sha256_of(path) != want:
            problems.append(Problem(rel, "sha256 mismatch"))
    return tuple(problems)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _parse_transponder(path: Path) -> Tuple[float, Dict[int, List[Tuple[float, float, float, float]]]]:
    """``timestamp,channel,ber,fec,power`` rows, unsorted in the file."""
    by_channel: Dict[int, List[Tuple[float, float, float, float]]] = {}
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip():
                continue
            t, ch, ber, fec, power = row[:5]
            by_channel.setdefault(int(ch), []).append(
                (float(t), float(ber), float(fec), float(power))
            )
    if not by_channel:
        raise HedgeDataError(f"{path}: no transponder rows")
    t0 = min(r[0] for rows in by_channel.values() for r in rows)
    for rows in by_channel.values():
        rows.sort()
    return t0, by_channel


_RATE_TO_GBPS = {
    "bits/sec": 1e-9, "Kbits/sec": 1e-6, "Mbits/sec": 1e-3, "Gbits/sec": 1.0,
}


_INTERVAL = re.compile(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$")


def _parse_iperf(path: Path, t0_epoch_s: float) -> Tuple[Tuple[Report, ...], Retiming]:
    """The ``[SUM]`` interval lines of one server log, re-timed.

    Lines look like ``<epoch ms>|[SUM]   0.00-1.00 sec  150 MBytes  1.26 Gbits/sec ...``.
    The stamp is the moment the line was flushed, not the moment the interval
    ended, and it trails by up to five seconds; the interval field is on
    iperf's own clock, which is anchored to the run's clock at the least-late
    line of the log (DECISIONS.md D14). The closing ``sender``/``receiver``
    totals are not intervals and are skipped.
    """
    raw: List[Tuple[float, float, float, float]] = []  # stamp, start, end, gbps
    with open(path, errors="replace") as fh:
        for line in fh:
            if "|" not in line or "[SUM]" not in line:
                continue
            stamp, text = line.split("|", 1)
            if "receiver" in text or "sender" in text:
                continue
            parts = text.split()
            # [SUM] a-b sec <bytes> <unit> <rate> <rate unit> ...
            if len(parts) < 7 or parts[2] != "sec" or parts[6] not in _RATE_TO_GBPS:
                continue
            interval = _INTERVAL.match(parts[1])
            if interval is None:
                continue
            gbps = float(parts[5]) * _RATE_TO_GBPS[parts[6]]
            raw.append((float(stamp) / 1000.0 - t0_epoch_s,
                        float(interval.group(1)), float(interval.group(2)), gbps))
    if not raw:
        raise HedgeDataError(f"{path}: no iperf interval lines")
    anchor = min(stamp - end for stamp, _, end, _ in raw)
    late = [stamp - end - anchor for stamp, _, end, _ in raw]
    reports = tuple(sorted(
        (Report(anchor + start, anchor + end, gbps) for _, start, end, gbps in raw),
        key=lambda r: r.t_s,
    ))
    return reports, Retiming(anchor, max(late),
                             sum(1 for x in late if x > REPORT_INTERVAL_S), len(raw))


def load_run(data_dir: Path, name: str,
             window: Optional[Tuple[float, float]] = None) -> Run:
    """Verify the run's five files against the manifest, then read them.

    ``window`` overrides the authors' analysis window for the run; pass
    ``(0.0, math.inf)`` to read a file end to end, including the further
    disturbances the notebooks leave outside their plots.
    """
    if name not in RUNS:
        raise KeyError(f"no HEDGE run named {name!r}; have {', '.join(RUNS)}")
    problems = verify(data_dir, name)
    if problems:
        listed = ", ".join(f"{p.path} ({p.what})" for p in problems)
        raise HedgeDataError(
            f"HEDGE data for run {name!r} is not usable: {listed}. "
            f"Run `make data` in a clone of the repository to fetch the pinned files "
            f"into {Path(data_dir)}; the pip package ships no data."
        )
    base = Path(data_dir) / name
    t0, by_channel = _parse_transponder(base / "transponder_data.csv")
    lo, hi = WINDOWS[name] if window is None else window
    labels = LABELS[name]
    wavelengths = []
    for channel in sorted(by_channel):
        samples = tuple(
            Sample(t - t0, ber, fec, power)
            for t, ber, fec, power in by_channel[channel]
            if lo <= t - t0 <= hi
        )
        wavelengths.append(Wavelength(channel, labels.get(channel), samples))
    parsed = [_parse_iperf(base / f, t0) for f in FILES[1:]]
    servers = tuple(reports for reports, _ in parsed)
    retiming = tuple(rt for _, rt in parsed)
    return Run(name, t0, (lo, hi), tuple(wavelengths), servers, retiming)


# --------------------------------------------------------------------------
# the authors' markers
# --------------------------------------------------------------------------


def first_counter_change(samples: Sequence[Sample]) -> Optional[int]:
    """Index of the first sample whose uncorrectable-FEC counter exceeds the
    first sample's. The authors' failure marker (their ``first_fec_change``)."""
    if not samples:
        return None
    initial = samples[0].fec
    for i in range(1, len(samples)):
        if samples[i].fec > initial:
            return i
    return None


def counter_restabilised(samples: Sequence[Sample], start: int, equal_run: int = 5) -> Optional[int]:
    """Index from which the counter holds still for ``equal_run`` further samples.

    The authors' recovery marker (their ``first_fec_change_stop``): from the
    failure index, the first sample followed by five equal readings.
    """
    for i in range(start, len(samples) - equal_run):
        if all(samples[i].fec == samples[i + j].fec for j in range(1, equal_run + 1)):
            return i
    return None


def link_down_intervals(
    servers: Sequence[Sequence[Report]], window: Tuple[float, float],
) -> Tuple[Tuple[float, Optional[float]], ...]:
    """When Layer 3 stopped carrying anything, on every server at once.

    Event-driven on each server's own re-timed reports: the link is down
    from the start of the interval that leaves every server's latest report
    at zero, until the start of the first interval on any server that carried
    something again. Interval starts, because traffic had already stopped by
    the start of an empty interval and had resumed by the end of a non-empty
    one; both edges are therefore early by less than one report interval,
    never late. The authors reduce the same logs by index, summing the four
    servers' i-th intervals; the registry checks that the two agree at every
    transition. An interval still open at the end of the window is returned
    with ``None`` as its end.
    """
    events = sorted(
        (r.t_s, r.start_s, k, r.gbps) for k, reports in enumerate(servers) for r in reports
    )
    latest: Dict[int, float] = {}
    down_since: Optional[float] = None
    out: List[Tuple[float, Optional[float]]] = []
    lo, hi = window
    for _, start, k, gbps in events:
        latest[k] = gbps
        all_quiet = len(latest) == len(servers) and all(v <= 0.0 for v in latest.values())
        if all_quiet and down_since is None:
            down_since = start
        elif not all_quiet and down_since is not None:
            if down_since <= hi and start >= lo:
                out.append((max(down_since, lo), min(start, hi)))
            down_since = None
    if down_since is not None and down_since <= hi:
        out.append((max(down_since, lo), None))
    return tuple(out)


# --------------------------------------------------------------------------
# per-wavelength timeline
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WavelengthTimeline:
    """One wavelength's run, in the authors' markers plus two of ours.

    ``onset_s`` is the last sample before the failure whose error rate was at
    or below the window's first sample --- the last moment the pre-FEC rate had
    not yet left its starting level. It has no free parameter, and it is this
    repository's marker, not the paper's; the registry prints what the
    obvious alternative does beside it. ``ramp_start`` is the sample after
    it, the first reading strictly above the start, which is where a curve
    that begins at zero errors first has a number to plot. ``cliff_s`` is the
    last sample before the counter moved, so the failure itself happened in
    ``(cliff_s, failed_s]``, a window one transponder poll wide.
    """

    channel: int
    label: Optional[str]
    ber_at_start: float
    power_at_start_dbm: float
    onset_s: Optional[float]
    ramp_start_s: Optional[float]
    ber_at_ramp_start: Optional[float]
    attenuation_at_ramp_start_db: Optional[float]
    failed_s: Optional[float]
    cliff_s: Optional[float]
    ber_at_cliff: Optional[float]
    attenuation_at_cliff_db: Optional[float]
    restabilised_s: Optional[float]
    samples: int

    @property
    def failed(self) -> bool:
        return self.failed_s is not None

    @property
    def onset_lead_s(self) -> Optional[float]:
        """Seconds from the error rate leaving its start to the counter moving."""
        if self.onset_s is None or self.failed_s is None:
            return None
        return self.failed_s - self.onset_s

    @property
    def failure_poll_gap_s(self) -> Optional[float]:
        """Width of the poll interval in which the counter moved: the data's resolution on the failure time."""
        if self.cliff_s is None or self.failed_s is None:
            return None
        return self.failed_s - self.cliff_s

    @property
    def name(self) -> str:
        return f"ch{self.channel}" + (f" {self.label}" if self.label else " (unlabelled)")


def timeline(w: Wavelength) -> WavelengthTimeline:
    s = w.samples
    if not s:
        raise HedgeDataError(f"channel {w.channel}: no samples inside the window")
    start = s[0]
    fail_i = first_counter_change(s)
    if fail_i is None:
        return WavelengthTimeline(
            w.channel, w.label, start.ber, start.power_dbm,
            None, None, None, None, None, None, None, None, None, len(s),
        )
    pre = s[:fail_i]
    onset_i = max(i for i in range(len(pre)) if pre[i].ber <= start.ber)
    ramp_i = onset_i + 1 if onset_i + 1 < len(pre) else onset_i
    cliff = pre[-1]
    restab_i = counter_restabilised(s, fail_i)
    return WavelengthTimeline(
        w.channel, w.label, start.ber, start.power_dbm,
        onset_s=pre[onset_i].t_s,
        ramp_start_s=pre[ramp_i].t_s,
        ber_at_ramp_start=pre[ramp_i].ber,
        attenuation_at_ramp_start_db=start.power_dbm - pre[ramp_i].power_dbm,
        failed_s=s[fail_i].t_s,
        cliff_s=cliff.t_s,
        ber_at_cliff=cliff.ber,
        attenuation_at_cliff_db=start.power_dbm - cliff.power_dbm,
        restabilised_s=None if restab_i is None else s[restab_i].t_s,
        samples=len(s),
    )


# --------------------------------------------------------------------------
# the one shape check against the drift model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScalingCheck:
    """How many dB the measured ramp took, against what each scaling predicts.

    The drift model turns optical margin into Q along one of two branches:
    10 dB per decade of Q, the thermal-noise-limited direct-detection
    relation it shipped with (``"direct"``), or 20 dB per decade, the
    OSNR-limited relation of an amplified link, in which Q squared follows
    the signal-to-noise ratio (``"coherent"``). Given the error rate where
    the ramp starts and where the cliff is, each branch predicts the dB
    between them; the measured attenuation says which is closer, and
    ``implied_db_per_decade`` says what slope the data itself has. Nothing is
    fitted, and "closer" is the whole claim: a measurement steeper than both
    branches is still closer to one of them.
    """

    channel: int
    label: Optional[str]
    ber_from: float
    ber_to: float
    measured_db: float
    direct_db: float
    coherent_db: float

    @property
    def closer(self) -> str:
        d = abs(self.measured_db - self.direct_db)
        c = abs(self.measured_db - self.coherent_db)
        if d == c:
            return "neither"
        return "coherent" if c < d else "direct"

    @property
    def decades_of_q(self) -> float:
        """How many decades Q fell between the ramp start and the cliff."""
        return self.direct_db / DB_PER_DECADE_OF_Q["direct"]

    @property
    def implied_db_per_decade(self) -> float:
        """The slope the measurement itself has, in dB per decade of Q."""
        return self.measured_db / self.decades_of_q

    @property
    def steeper_than_both(self) -> bool:
        return self.measured_db > max(self.direct_db, self.coherent_db)


def scaling_check(tl: WavelengthTimeline) -> Optional[ScalingCheck]:
    if (
        not tl.failed
        or tl.ber_at_ramp_start is None
        or tl.ber_at_cliff is None
        or tl.ber_at_ramp_start <= 0.0
        or tl.ber_at_cliff <= tl.ber_at_ramp_start
    ):
        return None
    measured = tl.attenuation_at_cliff_db - tl.attenuation_at_ramp_start_db
    return ScalingCheck(
        tl.channel, tl.label, tl.ber_at_ramp_start, tl.ber_at_cliff, measured,
        margin_span_db(tl.ber_at_ramp_start, tl.ber_at_cliff, detection="direct"),
        margin_span_db(tl.ber_at_ramp_start, tl.ber_at_cliff, detection="coherent"),
    )


# --------------------------------------------------------------------------
# run summary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    name: str
    window: Tuple[float, float]
    disturbance: str
    figure: str
    wavelengths: Tuple[WavelengthTimeline, ...]
    link_down: Tuple[Tuple[float, Optional[float]], ...]
    reports: int
    retiming: Tuple[Retiming, ...]
    probe_gbps_median: float

    def labelled(self) -> Tuple[WavelengthTimeline, ...]:
        return tuple(t for t in self.wavelengths if t.label is not None)

    def by_label(self, label: str) -> Tuple[WavelengthTimeline, ...]:
        return tuple(t for t in self.wavelengths if t.label == label)

    @property
    def failures(self) -> Tuple[WavelengthTimeline, ...]:
        return tuple(sorted((t for t in self.wavelengths if t.failed),
                            key=lambda t: t.failed_s))

    @property
    def first_failure_s(self) -> Optional[float]:
        f = self.failures
        return f[0].failed_s if f else None

    @property
    def last_failure_s(self) -> Optional[float]:
        f = self.failures
        return f[-1].failed_s if f else None

    @property
    def link_lost_s(self) -> Optional[float]:
        """When Layer 3 first went dark inside the window, if it did."""
        return self.link_down[0][0] if self.link_down else None

    @property
    def link_back_s(self) -> Optional[float]:
        return self.link_down[0][1] if self.link_down else None

    @property
    def link_up_at_window_end(self) -> bool:
        return all(end is not None for _, end in self.link_down)

    def link_up_throughout(self, until_s: float) -> bool:
        """No dark interval begins before ``until_s``."""
        return all(start >= until_s for start, _ in self.link_down)

    @property
    def lead_first_failure_to_link_loss_s(self) -> Optional[float]:
        if self.link_lost_s is None or self.first_failure_s is None:
            return None
        return self.link_lost_s - self.first_failure_s

    @property
    def lead_last_failure_to_link_loss_s(self) -> Optional[float]:
        """Negative when the outage began before the last wavelength's failure sample."""
        if self.link_lost_s is None or self.last_failure_s is None:
            return None
        return self.link_lost_s - self.last_failure_s

    @property
    def last_failure_poll_gap_s(self) -> Optional[float]:
        """The poll gap of the last wavelength to fail: the resolution on its failure time."""
        f = self.failures
        return f[-1].failure_poll_gap_s if f else None

    @property
    def outage_within_a_poll_of_last_failure(self) -> Optional[bool]:
        """Whether the outage began within one poll gap of the last failure sample, either side."""
        lead, gap = self.lead_last_failure_to_link_loss_s, self.last_failure_poll_gap_s
        if lead is None or gap is None:
            return None
        return abs(lead) <= gap


def summarize(run: Run) -> RunSummary:
    lo, hi = run.window
    carried = [r.gbps for reports in run.servers for r in reports
               if lo <= r.t_s <= hi and r.gbps > 0.0]
    return RunSummary(
        run.name, run.window, DISTURBANCE[run.name], FIGURES[run.name],
        tuple(timeline(w) for w in run.wavelengths),
        link_down_intervals(run.servers, run.window),
        sum(len(s) for s in run.servers),
        run.retiming,
        statistics.median(carried) if carried else 0.0,
    )


def load_summaries(data_dir: Path, runs: Iterable[str] = RUNS) -> Dict[str, RunSummary]:
    return {name: summarize(load_run(data_dir, name)) for name in runs}


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _s(x: Optional[float], unit: str = " s", width: int = 7) -> str:
    return f"{'-':>{width}}" if x is None else f"{x:>{width}.1f}{unit}"


def report(summaries: Dict[str, RunSummary]) -> str:
    lines: List[str] = []
    lines.append(f"HEDGE testbed, {HEDGE_REPO} at {HEDGE_COMMIT[:12]}")
    lines.append("times are seconds after the first transponder sample of each run; iperf")
    lines.append("intervals are re-timed from iperf's own interval field, because each log")
    lines.append("line's stamp is a stdout flush, up to five seconds late (DECISIONS.md D14);")
    lines.append("the transponder is polled about every 1.6 s on a clock whose agreement with")
    lines.append("the iperf hosts' is undocumented, so read a cross-clock lead to the nearest poll")
    for name, s in summaries.items():
        lo, hi = s.window
        lines.append("")
        lines.append(f"run {name}: {s.disturbance} ({s.figure}); window {lo:g}-{hi:g} s")
        lines.append(f"  iperf: {s.reports} intervals on {len(s.retiming)} servers, probes carried "
                     f"a median {s.probe_gbps_median:.3f} Gbit/s each; stamps trailed their "
                     f"interval by up to {max(rt.max_late_s for rt in s.retiming):.1f} s")
        lines.append(f"  {'wavelength':<22} {'BER start':>10} {'onset':>9} {'failed':>9} "
                     f"{'cliff BER':>10} {'atten':>8} {'restab':>9}")
        for t in s.wavelengths:
            head = (f"  {t.name:<22} {t.ber_at_start:>10.2e} {_s(t.onset_s):>9} "
                    f"{_s(t.failed_s):>9}")
            if t.ber_at_cliff is None:
                lines.append(f"{head} {'-':>10} {'-':>8} {'-':>9}")
            else:
                lines.append(f"{head} {t.ber_at_cliff:>10.2e} "
                             f"{t.attenuation_at_cliff_db:>6.2f}dB {_s(t.restabilised_s):>9}")
        if not s.link_down:
            lines.append("  layer 3: carried traffic for the whole window")
        else:
            for start, end in s.link_down:
                lines.append(f"  layer 3: dark from {start:.1f} s "
                             + (f"to {end:.1f} s" if end is not None else "to the end of the window"))
        if s.lead_first_failure_to_link_loss_s is not None:
            last = s.lead_last_failure_to_link_loss_s
            lines.append(
                f"  lead: the first FEC alarm came {s.lead_first_failure_to_link_loss_s:.1f} s "
                f"before the outage; the outage began {abs(last):.1f} s "
                f"{'after' if last >= 0 else 'before'} the last wavelength's alarm sample, "
                f"whose poll gap was {s.last_failure_poll_gap_s:.1f} s"
            )
        checks = [c for c in (scaling_check(t) for t in s.wavelengths) if c is not None]
        for c in checks:
            lines.append(
                f"  scaling {('ch%d ' % c.channel) + (c.label or ''):<16} BER {c.ber_from:.1e} -> "
                f"{c.ber_to:.1e} took {c.measured_db:.1f} dB, {c.implied_db_per_decade:.1f} dB "
                f"per decade of Q; direct predicts {c.direct_db:.1f}, coherent "
                f"{c.coherent_db:.1f}: closer to {c.closer}"
                + (", steeper than both" if c.steeper_than_both else "")
            )
    return "\n".join(lines)
