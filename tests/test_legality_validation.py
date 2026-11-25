"""Retune input refusal and legitimate output-sentinel contracts."""
import contextlib
from dataclasses import replace
import io
import math
import unittest
from ocintent.intent import Boundary
from ocintent.legality import (JobRhythm, Legality, assess, ladder, disagreement_intervals,
                               disagreement_width_s, RetuneCost)
from ocintent.cli import main


def rhythm(**changes):
    fields=dict(accelerators=16384,step_s=2.4,cross_stitch_collective_s=.31,
                steps_per_checkpoint=250,checkpoint_window_s=120.,checkpoint_crosses_stitch=False,
                steps_per_epoch=4000,epoch_gap_s=45.)
    fields.update(changes)
    return JobRhythm(**fields)


class LegalityValidationTests(unittest.TestCase):
    def test_invalid_retune_never_becomes_legal(self):
        for value in (math.nan,math.inf,-math.inf,-1,True,'1',None,1j,10**400):
            for boundary in Boundary:
                with self.subTest(value=repr(value),boundary=boundary):
                    with self.assertRaisesRegex(ValueError,'retune_s'): assess(rhythm(),value,boundary)

    def test_rhythm_types_and_domains(self):
        for field in ('step_s','checkpoint_window_s','epoch_gap_s','collective_timeout_s','remaining_s','cross_stitch_collective_s'):
            for value in (math.nan,math.inf,-math.inf,-1,True,'1',None,1j,10**400):
                with self.subTest(field=field,value=repr(value)):
                    with self.assertRaisesRegex(ValueError,field): replace(rhythm(),**{field:value})
            if field!='cross_stitch_collective_s':
                with self.assertRaisesRegex(ValueError,field): rhythm(**{field:0})
        for field in ('accelerators','steps_per_checkpoint','steps_per_epoch'):
            for value in (0,-1,True,1.5,'2',None,10**400):
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
        probes=[lambda:rhythm(step_s=1e308,checkpoint_window_s=1e308).quiet_window_s(Boundary.BETWEEN_CHECKPOINTS),
                lambda:rhythm(step_s=1e308).mean_wait_s(Boundary.BETWEEN_EPOCHS),
                lambda:assess(rhythm(accelerators=10**308,collective_timeout_s=1e308),1e308,Boundary.MID_COLLECTIVE),
                lambda:disagreement_intervals(rhythm(collective_timeout_s=1e308)),
                lambda:RetuneCost(Boundary.BETWEEN_STEPS,Legality.STALL,0,1e308,1e308,1).total_delay_s]
        for probe in probes:
            with self.assertRaisesRegex(ValueError,'finite numeric range'): probe()

    def test_cli_refusal_has_no_partial_output_and_recovers(self):
        for args in (['ladder','--retune-s','nan'],['ladder','--retune-s','inf'],['ladder','--step-s','1e308'],['disagree','--step-s','nan'],['disagree','--step-s','1e308']):
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code=main(args)
            self.assertEqual(code,2);self.assertEqual(out.getvalue(),'');self.assertIn('unreadable retune input:',err.getvalue());self.assertNotIn('Traceback',err.getvalue())
        for command in ('ladder','disagree'):
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code=main([command])
            self.assertEqual(code,0);self.assertTrue(out.getvalue());self.assertEqual(err.getvalue(),'')
