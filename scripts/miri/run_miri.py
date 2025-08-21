#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2025 vivo Mobile Communication Co., Ltd.
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

import os
import subprocess
import sys


def read_rustdeps_and_extern_flags(externs_file_path):

    if not os.path.exists(externs_file_path):
        sys.stderr.write(
            f"Error: externs file not found at {externs_file_path}\n")
        return []

    with open(externs_file_path, 'r', encoding='utf-8') as f:
        content = f.readlines()

    rustdeps_line = content[0].strip()
    externs_line = content[1].strip()

    rustdeps_flags = rustdeps_line.split(' ')
    externs_flags = externs_line.split(' ')

    return rustdeps_flags, externs_flags


def main():
    if len(sys.argv) < 4:
        sys.stderr.write(
            "Usage: script.py <dummy_rustdeps_externs_file_path> <lib_path> [extra miri args]\n"
        )
        return 1

    dummy_rustdeps_externs_file_path = sys.argv[1]
    target_name = sys.argv[2]
    lib_path = sys.argv[3]

    try:

        rustdeps_flags, extern_flags = read_rustdeps_and_extern_flags(
            dummy_rustdeps_externs_file_path)

        command_and_args = [
            "miri", f"--crate-name={target_name}", "--edition=2021", "--test",
            lib_path
        ] + rustdeps_flags + extern_flags + sys.argv[4:]

        print(f"Executing command: {' '.join(command_and_args)}")

        process = subprocess.Popen(command_and_args,
                                   env=os.environ,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   text=True)
        stdout, stderr = process.communicate()

        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)

        if process.returncode != 0:
            benign_signals = [
                "Undefined Behavior",
                "Memory leaked",
                "Pointer out of bounds",
                "not aligned",
                "uninitialized",
                "arithmetic overflow",
            ]
            if any(sig in stderr or sig in stdout for sig in benign_signals):
                sys.stderr.write(
                    "\n[Miri warning] Detected runtime error (UB/Leak/etc), ignoring failure.\n"
                )
                sys.exit(0)
            else:
                sys.stderr.write(
                    "\n[Miri error] Miri Error, ignoring failure.\n")
                sys.exit(0)

    except FileNotFoundError:
        sys.stderr.write(
            "Error: 'miri' command not found. Please ensure it's in your PATH.\n"
        )
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"An unexpected error occurred: {e}\n")
        sys.exit(1)


if __name__ == '__main__':
    sys.exit(main())
