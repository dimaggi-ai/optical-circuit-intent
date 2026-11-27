"""Retune input refusal and legitimate output-sentinel contracts."""
import contextlib
from dataclasses import replace
import io
import math
import unittest
from unittest.mock import patch
from ocintent.intent import Boundary
from ocintent.legality import (JobRhythm, Legality, assess, ladder, disagreement_intervals,
                               disagreement_width_s, RetuneCost, _breakpoints)
from ocintent.cli import main


def rhythm(**changes):
    fields=dict(accelerators=16384,step_s=2.4,cross_stitch_collective_s=.31,
                steps_per_checkpoint=250,checkpoint_window_s=120.,checkpoint_crosses_stitch=False,
                steps_per_epoch=4000,epoch_gap_s=45.)
    fields.update(changes)
    return JobRhythm(**fields)


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


class LegalityValidationTests(unittest.TestCase):
    def test_invalid_retune_never_becomes_legal(self):
        for value in (math.nan,math.inf,-math.inf,-1,True,False,IntSubclass(1),FloatSubclass(1),'1',None,1j,10**400):
            for boundary in Boundary:
                with self.subTest(value=repr(value),boundary=boundary):
                    with self.assertRaisesRegex(ValueError,'retune_s'): assess(rhythm(),value,boundary)

    def test_rhythm_types_and_domains(self):
        for field in ('step_s','checkpoint_window_s','epoch_gap_s','collective_timeout_s','remaining_s','cross_stitch_collective_s'):
            for value in (math.nan,math.inf,-math.inf,-1,True,False,IntSubclass(1),FloatSubclass(1),'1',None,1j,10**400):
                with self.subTest(field=field,value=repr(value)):
                    with self.assertRaisesRegex(ValueError,field): replace(rhythm(),**{field:value})
            if field!='cross_stitch_collective_s':
                with self.assertRaisesRegex(ValueError,field): rhythm(**{field:0})
        for field in ('accelerators','steps_per_checkpoint','steps_per_epoch'):
            for value in (0,-1,True,False,IntSubclass(1),FloatSubclass(1),1.5,'2',None,10**400):
                with self.assertRaisesRegex(ValueError,field): rhythm(**{field:value})
        for value in ('false',0,1,None):
            with self.assertRaisesRegex(ValueError,'checkpoint_crosses_stitch'):
                rhythm(checkpoint_crosses_stitch=value)
        with self.assertRaises(ValueError): rhythm(cross_stitch_collective_s=2.4)

    def test_valid_boundaries_and_output_sentinels(self):
        r=rhythm()
        self.assertEqual(assess(r,600,Boundary.MID_COLLECTIVE).legality,Legality.STALL)
        self.assertEqual(assess(r,601,Boundary.MID_COLLECTIVE).legality,Legality.FATAL)
        self.assertTrue(math.isinf(assess(r,601,Boundary.MID_COLLECTIVE).lost_accelerator_hours))
        self.assertTrue(math.isinf(r.quiet_window_s(Boundary.BETWEEN_JOBS)))
        self.assertTrue(all(c.legality==Legality.INVISIBLE for c in ladder(r,0)))
        self.assertEqual(disagreement_intervals(r),((2.09,767.09),))
        self.assertEqual(disagreement_width_s(r),765.)
        self.assertGreater(r.quiet_window_s(Boundary.BETWEEN_CHECKPOINTS),replace(r,checkpoint_crosses_stitch=True).quiet_window_s(Boundary.BETWEEN_CHECKPOINTS))

    def test_derived_range_refuses_without_fabricated_infinity(self):
        probes = [
            ('quiet_window_s', lambda: rhythm(step_s=1e308, checkpoint_window_s=1e308).quiet_window_s(Boundary.BETWEEN_CHECKPOINTS)),
            ('mean_wait_s', lambda: rhythm(step_s=1e308).mean_wait_s(Boundary.BETWEEN_EPOCHS)),
            ('lost_accelerator_hours', lambda: assess(rhythm(accelerators=10**308, collective_timeout_s=1e308), 1e308, Boundary.MID_COLLECTIVE)),
            ('total_delay_s', lambda: RetuneCost(Boundary.BETWEEN_STEPS, Legality.STALL, 0, 1e308, 1e308, 1).total_delay_s),
        ]
        for quantity, probe in probes:
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(ValueError, quantity + ' exceeds the finite numeric range'):
                    probe()

    def test_each_epoch_window_addition_refuses_overflow(self):
        # The first case overflows idle + epoch; the second keeps that sum
        # finite and overflows only when the checkpoint window is added.
        for changes in (
            dict(step_s=1e308, epoch_gap_s=1e308, checkpoint_crosses_stitch=True),
            dict(step_s=1e308, epoch_gap_s=1., checkpoint_window_s=1e308),
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, 'quiet_window_s exceeds the finite numeric range'):
                    rhythm(**changes).quiet_window_s(Boundary.BETWEEN_EPOCHS)

    def test_integer_derived_overflow_is_a_named_value_error(self):
        # Each integer input fits a float; their integer sum does not. This
        # exercises OverflowError normalization, not float infinity detection.
        with self.assertRaisesRegex(ValueError, 'quiet_window_s exceeds the finite numeric range'):
            rhythm(step_s=10**308, cross_stitch_collective_s=0,
                   epoch_gap_s=10**308, checkpoint_crosses_stitch=True).quiet_window_s(Boundary.BETWEEN_EPOCHS)

    def test_checkpoint_wait_and_assess_total_delay_refuse_immediately(self):
        with self.assertRaisesRegex(ValueError, 'mean_wait_s exceeds the finite numeric range'):
            rhythm(step_s=1e308).mean_wait_s(Boundary.BETWEEN_CHECKPOINTS)
        with self.assertRaisesRegex(ValueError, 'total_delay_s exceeds the finite numeric range'):
            assess(rhythm(accelerators=1, step_s=1.7e308, cross_stitch_collective_s=1.6e308,
                          collective_timeout_s=1.7e308), 1.2e308, Boundary.BETWEEN_STEPS)

    def test_breakpoint_additions_name_the_overflow(self):
        fields = dict(accelerators=1, step_s=1e308, cross_stitch_collective_s=0,
                      steps_per_checkpoint=1, checkpoint_window_s=1,
                      steps_per_epoch=1, epoch_gap_s=1)
        # Quiet + timeout, then quiet + another boundary's wait. The direct
        # breakpoint API avoids an earlier cost calculation masking the guard.
        for extra in (dict(collective_timeout_s=1e308, remaining_s=1),
                      dict(collective_timeout_s=1, remaining_s=1.7e308)):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ValueError, 'breakpoint exceeds the finite numeric range'):
                    _breakpoints(rhythm(**fields, **extra))

    def test_midpoint_and_tail_probes_name_the_overflow(self):
        midpoint = rhythm(accelerators=1, step_s=1e308, cross_stitch_collective_s=0,
                          steps_per_checkpoint=1, checkpoint_window_s=1,
                          steps_per_epoch=1, epoch_gap_s=1,
                          collective_timeout_s=5e307, remaining_s=1)
        tail = rhythm(accelerators=1, collective_timeout_s=1e308)
        for name, value in (('midpoint', midpoint), ('tail', tail)):
            with self.subTest(probe=name):
                with self.assertRaisesRegex(ValueError, 'probe exceeds the finite numeric range'):
                    disagreement_intervals(value)
        # The final tail probe also overflows for this rhythm. Observe the
        # selector boundary separately so that a later tail refusal cannot
        # mask a midpoint guard deletion. This is a boundary-isolation test,
        # not a claim that the isolated selector computes the real verdict.
        observed = []
        def observe_selector(_rhythm, retune_s):
            self.assertTrue(math.isfinite(retune_s))
            observed.append(retune_s)
            return None
        with patch('ocintent.legality.objectives_disagree', side_effect=observe_selector):
            with self.assertRaisesRegex(ValueError, 'probe exceeds the finite numeric range'):
                disagreement_intervals(midpoint)
        self.assertTrue(observed)
        # A width-overflow input is not fabricated: the returned finite
        # intervals are ordered/disjoint and their total measure is bounded
        # by the largest finite endpoint. The width guard is defensive.

    def test_cli_refusal_has_no_partial_output_and_recovers(self):
        for args in (['ladder','--step-s','nan'],['ladder','--retune-s','nan'],['ladder','--retune-s','inf'],['ladder','--step-s','1e308'],['disagree','--step-s','nan'],['disagree','--step-s','1e308']):
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code=main(args)
            self.assertEqual(code,2);self.assertEqual(out.getvalue(),'');self.assertIn('unreadable retune input:',err.getvalue());self.assertNotIn('Traceback',err.getvalue())
        for command in ('ladder','disagree'):
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code=main([command])
            self.assertEqual(code,0);self.assertTrue(out.getvalue());self.assertEqual(err.getvalue(),'')
