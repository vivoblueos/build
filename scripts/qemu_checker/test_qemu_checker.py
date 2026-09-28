#!/usr/bin/env python3
# Copyright (c) 2026 vivo Mobile Communication Co., Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import io
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest

from qemu_checker import Checker


class CheckerTests(unittest.TestCase):

    def run_guest(self, source):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'guest.py'
            script.write_text(f'#!{sys.executable}\n' + source)
            script.chmod(0o755)
            checker = Checker(str(script), directory)
            checker.set_newline_timeout(3).set_total_timeout(5)
            checker.add_assert_succ(re.compile(r'^Done kernel unittests$'))
            checker.add_assert_fail(re.compile(r'^Oops:'))
            output = io.StringIO()
            # Expected guest failures are test data, so capture their logs too.
            with self.assertLogs('qemu_checker', level='INFO'), \
                    contextlib.redirect_stdout(output):
                result = checker.run_and_check()
            return result, output.getvalue(), checker.fail_lines

    def test_panic_preserves_delayed_message_and_diagnostics(self):
        result, output, failures = self.run_guest('''
import time
print('panicked at tinyarc/mod.rs:205:9:', flush=True)
time.sleep(0.05)
print('assertion failed: old >= 1', flush=True)
print('Oops: assertion failed: old >= 1', flush=True)
print('Memory: total=1024 used=512 max=768', flush=True)
print('Done kernel unittests', flush=True)
time.sleep(60)
''')
        self.assertEqual(result, -1)
        self.assertIn('assertion failed: old >= 1', output)
        self.assertIn('Memory: total=1024 used=512 max=768', output)
        self.assertIn('Oops: assertion failed: old >= 1\n', failures)

    def test_panic_without_message_stops_within_grace_period(self):
        start = time.monotonic()
        result, output, _ = self.run_guest('''
import time
print('panicked at main.rs:1:1:', flush=True)
time.sleep(60)
''')
        self.assertEqual(result, -1)
        self.assertIn('panicked at main.rs:1:1:', output)
        self.assertLess(time.monotonic() - start, 3)

    def test_panic_reaches_eof_after_message(self):
        result, output, _ = self.run_guest('''
print('panicked at main.rs:1:1:', flush=True)
print('explicit panic message', flush=True)
''')
        self.assertEqual(result, -1)
        self.assertIn('explicit panic message', output)

    def test_success_still_ends_the_check(self):
        result, _, _ = self.run_guest('''
import time
print('Done kernel unittests', flush=True)
time.sleep(60)
''')
        self.assertEqual(result, 0)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CheckerTests)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    # GN checks are silent on success; manual runs still show the test report.
    if len(sys.argv) == 1 or not result.wasSuccessful():
        sys.stderr.write(output.getvalue())
    if not result.wasSuccessful():
        sys.exit(1)
    if len(sys.argv) > 1:
        stamp = Path(sys.argv[1])
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
