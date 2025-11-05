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


def get_all_deps(target, out_dir, cur_cwd):

    command_and_args = ["gn", "desc", out_dir, target, "deps", "--all"]
    result = subprocess.run(
        command_and_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cur_cwd
    )  # collect all deps' lib.rs path (including direct deps and indirect deps)
    if result.returncode != 0:
        sys.stderr.write(
            f"gn desc failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
        )
        sys.exit(result.returncode)

    deps = [
        line.strip() for line in result.stdout.splitlines()
        if line.strip().startswith("//")
    ]
    return deps


def deps_to_extern_flags(deps, out_dir):
    flags = []
    for dep in deps:

        crate_name = dep.split(":")[-1]
        rlib_path = os.path.join(out_dir, "obj",
                                 dep.strip("//").replace(":", "/"),
                                 f"lib{crate_name}.rlib"
                                 )  # same as in blueos.gni toolchain template

        flags.extend(["--extern", f"{crate_name}={rlib_path}"])
    return flags


def main():
    if len(sys.argv) < 5:
        sys.stderr.write(
            "Usage: script.py <target> <outdir> <project_root> <path_to_lib.rs> [extra miri args]\n"
        )
        return 1

    target = sys.argv[1]
    out_dir = sys.argv[2]
    project_root = sys.argv[3]
    lib_path = sys.argv[4]

    try:
        deps = get_all_deps(target, out_dir, project_root)

        extern_flags = deps_to_extern_flags(deps, out_dir)

        command_and_args = ["miri", "--edition=2021", "--test", lib_path
                            ] + extern_flags + sys.argv[5:]

        print(f"Executing command: {' '.join(command_and_args)}")

        process = subprocess.Popen(command_and_args,
                                   env=os.environ,
                                   cwd=project_root)
        process.wait()

        if process.returncode != 0:
            sys.stderr.write(
                f"\nCommand failed with exit code {process.returncode}\n")
            sys.exit(process.returncode)

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
