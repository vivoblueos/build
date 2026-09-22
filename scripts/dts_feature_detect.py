#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR Apache-2.0

"""
Phase 1 DTS feature detection for GN exec_script.

Scans a DTS file for enabled devices, consults YAML bindings, and
emits --cfg dt_* rustflags to stdout.  Called by GN at gen time.

Uses CPP preprocessing to resolve #include directives before feeding
the DTS to dtc (Zephyr-style approach).
"""

import argparse
import os
import subprocess
import sys

try:
    import yaml
except ImportError:
    yaml = None


def _munge_compat(compat: str) -> str:
    return compat.replace(",", "_").replace("-", "_").replace(".", "_")


def _preprocess_dts(cpp_path: str, dts_file: str, dts_dir: str) -> str:
    """Run CPP preprocessor on a DTS file.

    Uses -x assembler-with-cpp to prevent CPP from choking on DTS syntax
    like '#address-cells', '#size-cells', or '@' in node names.
    The DTS file is passed via -include; /dev/null is the positional input.
    """
    # Include the directory of the DTS file (for #include relative to the .dts),
    # the dts-dir itself, and arch-specific subdirectories.
    dts_base_dir = os.path.dirname(os.path.abspath(dts_file))
    cmd = [
        cpp_path,
        "-x", "assembler-with-cpp",
        "-nostdinc",
        "-undef",
        "-P",
        "-I", dts_base_dir,
        "-I", dts_dir,
        "-I", os.path.join(dts_dir, "riscv"),
        "-I", os.path.join(dts_dir, "arm"),
        "-include", dts_file,
        "/dev/null",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"cpp failed: {result.stderr}")
    return result.stdout


def _walk_yaml(node, path):
    """Yield (path, props_dict) for every node with 'compatible'.

    dtc -O yaml produces a nested dict structure where:
    - Properties are key-value pairs (value may be list, string, or int)
    - Child nodes have keys that start with '@' or are plain names
    """
    props = {}
    child_nodes = {}

    for key, val in node.items():
        if isinstance(val, dict):
            child_nodes[key] = val
        else:
            props[key] = val

    if "compatible" in props:
        yield path, props
    for name, child in child_nodes.items():
        yield from _walk_yaml(child, f"{path}/{name}")


def _phandle_constructor(loader, node):
    return loader.construct_scalar(node)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dts", required=True)
    parser.add_argument("--dts-dir", required=True)
    parser.add_argument("--cpp", default="cpp")
    args = parser.parse_args()

    # Register YAML constructor for dtc's !phandle tag.
    if yaml is not None:
        yaml.add_constructor("!phandle", _phandle_constructor, Loader=yaml.Loader)

    # Phase 1 is best-effort -- if dtc or cpp is not available, silently skip.
    try:
        preprocessed = _preprocess_dts(args.cpp, args.dts, args.dts_dir)
        result = subprocess.run(
            ["dtc", "-I", "dts", "-O", "yaml", "-"],
            input=preprocessed,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            sys.exit(0)
        tree = yaml.load(result.stdout, Loader=yaml.Loader)
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError):
        sys.exit(0)
    except Exception:
        sys.exit(0)

    if tree is None or yaml is None:
        sys.exit(0)

    # Load bindings.
    bindings_dir = os.path.join(args.dts_dir, "bindings")
    bindings = {}
    if os.path.isdir(bindings_dir):
        for root, _dirs, files in os.walk(bindings_dir):
            for f in files:
                if f.endswith((".yaml", ".yml")):
                    path = os.path.join(root, f)
                    with open(path) as fh:
                        try:
                            b = yaml.safe_load(fh)
                            if b and "compatible" in b:
                                bindings[b["compatible"]] = b
                        except yaml.YAMLError:
                            pass

    nodes = []
    for entry in tree if isinstance(tree, list) else [tree]:
        nodes.extend(list(_walk_yaml(entry, "")))

    seen = set()
    for _path, props in nodes:
        compat_raw = props.get("compatible", "")
        if isinstance(compat_raw, list):
            compat = compat_raw[0]
        elif isinstance(compat_raw, str):
            compat = compat_raw
        else:
            continue
        if not compat:
            continue
        # dtc YAML: status is either a plain string or a single-element list.
        status = props.get("status", "")
        if isinstance(status, list):
            status = status[0] if status else ""
        if status and status != "okay":
            continue
        binding = bindings.get(compat)
        if binding is None:
            continue
        for flag_tpl in binding.get("cfg-flags", []):
            label = _munge_compat(compat)
            flag = flag_tpl.replace("${compatible_label}", label)
            if flag not in seen:
                # Emit --cfg and flag on separate lines so GN's "list lines"
                # mode produces ["--cfg", "dt_has_*"] rather than ["--cfg dt_has_*"].
                print("--cfg")
                print(flag)
                seen.add(flag)


if __name__ == "__main__":
    main()