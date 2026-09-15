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
"""Generate the artifact-to-VFS layout for a boot-seeded app bundle."""

import argparse
import json
import os


def parse_file(spec):
    vfs_path, separator, artifact = spec.partition(",")
    if not separator or not vfs_path.startswith("/") or not artifact:
        raise SystemExit(f"invalid VFS path,artifact pair: {spec!r}")
    if not os.path.isfile(artifact):
        raise SystemExit(f"bundle artifact missing: {artifact}")
    return {"vfs_path": vfs_path, "artifact": artifact}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="VFS path,artifact pair")
    parser.add_argument("--file",
                        action="append",
                        default=[],
                        help="additional VFS path,artifact pair")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    layout = [parse_file(args.root)]
    layout.extend(parse_file(spec) for spec in args.file)
    paths = [entry["vfs_path"] for entry in layout]
    if len(paths) != len(set(paths)):
        raise SystemExit("bundle contains duplicate VFS paths")
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(layout, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
