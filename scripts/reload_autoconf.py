#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
"""
Reload .config file to get the latest configuration for generating rustflags
"""

import os
import argparse
from kconfiglib import Kconfig
from pathlib import Path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kconfig", help="Kconfig path")
    parser.add_argument("--board", help="target board")
    parser.add_argument("--autoconf", help="config file path")
    parser.add_argument("--app_conf", help="app config file path")
    parser.add_argument("--stamp_file", help="stamp file path")
    args = parser.parse_args()
    os.environ["BOARD"] = args.board
    os.environ["KCONFIG_DIR"] = os.path.dirname(args.kconfig)
    # Set KERNEL_SRC_DIR to point to kernel/kernel/src directory
    kconfig_dir = os.path.dirname(args.kconfig)
    kernel_src_dir = os.path.join(
        os.path.dirname(os.path.dirname(kconfig_dir)), "kernel", "src")
    os.environ["KERNEL_SRC_DIR"] = os.path.abspath(kernel_src_dir)
    try:
        kconf = Kconfig(args.kconfig)
        kconf.load_config(args.autoconf)
        if args.app_conf:
            # app_conf is an overlay fragment: overriding existing values is expected.
            kconf.disable_override_warnings()
            kconf.disable_redun_warnings()
            kconf.load_config(args.app_conf, replace=False)
        kconf.write_config(args.autoconf)
        with open(args.stamp_file, "w", encoding="utf-8") as stamp:
            pass
    except Exception as e:
        print(f"Error loading Kconfig: {e}")
        exit(1)
