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
This file will reload kconfig, modify .config again, and call 
ninja to recompile the target, rebuilding the dependency tree.
Note: This script will modified the .config globally, so it should be used carefully.
"""

import os
import argparse
from kconfiglib import Kconfig
import subprocess


def reload_kconfig(kconfig_path, autoconf_path, app_conf_path):
    kconf = Kconfig(kconfig_path)
    kconf.load_config(autoconf_path)
    kconf.disable_override_warnings()
    kconf.disable_redun_warnings()
    kconf.load_config(app_conf_path, replace=False)
    kconf.write_config(autoconf_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kconfig", help="Kconfig path")
    parser.add_argument("--board", help="target board")
    parser.add_argument("--autoconf", help="config file path")
    parser.add_argument("--app_conf",
                        help="app config file path",
                        default=None)
    parser.add_argument("-C", "--ninja_dir", help="ninja build directory")
    parser.add_argument("target_name", help="ninja target name")
    args = parser.parse_args()
    os.environ["BOARD"] = args.board
    os.environ["KCONFIG_DIR"] = os.path.dirname(args.kconfig)
    kconfig_dir = os.path.dirname(args.kconfig)
    kernel_src_dir = os.path.join(
        os.path.dirname(os.path.dirname(kconfig_dir)), "kernel", "src")
    os.environ["KERNEL_SRC_DIR"] = os.path.abspath(kernel_src_dir)
    try:
        if args.app_conf:
            reload_kconfig(args.kconfig, args.autoconf, args.app_conf)

        ninja_cmd = ['ninja', '-C', args.ninja_dir, args.target_name]
        subprocess.run(ninja_cmd, check=True)

    except Exception as e:
        print(f"Error reload autoconf and build: {e}")
        exit(1)
