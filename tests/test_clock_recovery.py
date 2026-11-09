import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('jetson_clock_test',
    Path(__file__).resolve().parents[1]/'deploy/clock_recover.py')
clock=importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


class ClockRecoveryTests(unittest.TestCase):
    def test_independent_agreement_marks_this_boot_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            state,verified=Path(folder)/'state',Path(folder)/'verified'
            with patch.object(clock,'STATE',state),patch.object(clock,'VERIFIED',verified),patch.object(clock,'TIMESYNC',Path(folder)/'ntp'),\
                 patch.object(clock,'source_time',side_effect=[clock.FLOOR+100,clock.FLOOR+102]),\
                 patch.object(clock.subprocess,'run') as run:
                clock.main()
                self.assertEqual(verified.read_text(),str(clock.FLOOR+102))
                self.assertEqual(state.read_text(),verified.read_text())
                run.assert_called_once()

    def test_network_failure_keeps_floor_but_does_not_enable_paid_work(self):
        with tempfile.TemporaryDirectory() as folder:
            state,verified=Path(folder)/'state',Path(folder)/'verified'
            state.write_text(str(clock.FLOOR+100))
            with patch.object(clock,'STATE',state),patch.object(clock,'VERIFIED',verified),\
                 patch.object(clock,'source_time',side_effect=OSError('offline')),\
                 patch.object(clock.subprocess,'run'),patch.object(clock.time,'time',return_value=0):
                with self.assertRaises(SystemExit): clock.main()
                self.assertFalse(verified.exists())
                self.assertEqual(state.read_text(),str(clock.FLOOR+100))

    def test_disagreeing_or_implausibly_old_dates_fail_closed(self):
        for samples in ([clock.FLOOR+100,clock.FLOOR+500,clock.FLOOR+1000],
                        [clock.FLOOR-500]*3):
            with tempfile.TemporaryDirectory() as folder:
                state,verified=Path(folder)/'state',Path(folder)/'verified'
                with patch.object(clock,'STATE',state),patch.object(clock,'VERIFIED',verified),\
                     patch.object(clock,'source_time',side_effect=samples),patch.object(clock.subprocess,'run'):
                    with self.assertRaises(SystemExit): clock.main()
                    self.assertFalse(verified.exists())


if __name__=='__main__': unittest.main()
