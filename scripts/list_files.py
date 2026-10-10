#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR Apache-2.0

import glob
import os
import sys

for path in sys.argv[1:]:
    for f in sorted(glob.glob(os.path.join(path, "**/*.yaml"), recursive=True)):
        print(f)
    for f in sorted(glob.glob(os.path.join(path, "**/*.yml"), recursive=True)):
        print(f)