#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR Apache-2.0

"""
Phase 2 DTS code generator for Ninja action.

Reads a DTS file + YAML bindings and emits:
  <out-dir>/mod.rs
  <out-dir>/<compat-name>.rs   (one per enabled device)

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

_RUST_HEADER = "// Auto-generated from device tree. DO NOT EDIT.\n\n"

# dtc -O yaml output format (dtc 1.7.0):
# - Simple integer properties: a one-element list [3686400]
# - String properties: a plain string
# - Reg properties: a list of lists, each inner list is [addr_hi, addr_lo, size_hi, size_lo]
#   e.g. [[0, 268435456, 0, 256]] for reg = <0x0 0x10000000 0x0 0x100>
# - Interrupts: [[10]]


def _munge_compat(compat: str) -> str:
    return compat.replace(",", "_").replace("-", "_").replace(".", "_")


def _preprocess_dts(cpp_path: str, dts_file: str, dts_dir: str) -> str:
    """Run CPP preprocessor on a DTS file.

    Uses -x assembler-with-cpp to prevent CPP from choking on DTS syntax
    like '#address-cells', '#size-cells', or '@' in node names.
    The DTS file is passed via -include; /dev/null is the positional input.
    """
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


def _compat_to_modname(compat: str) -> str:
    """Convert a compatible string to a Rust module name for generated files.

    Strip a single trailing digit if preceded by a non-digit character.
    This handles version suffixes like "plic0" -> "plic" while preserving
    names where digits are integral like "ns16550".
    """
    name = _munge_compat(compat)
    if len(name) >= 2 and name[-1].isdigit() and not name[-2].isdigit():
        name = name[:-1]
    return name


def _walk_dtc_yaml(node, path):
    """Yield (path, properties_dict) for every node with 'compatible'.

    dtc -O yaml produces a nested dict structure where:
    - Properties are key-value pairs (value may be list, string, or int)
    - Child nodes have keys that may start with '@' for unit addresses
    """
    props = {}
    child_nodes = {}

    for key, val in node.items():
        if isinstance(val, dict):
            child_nodes[key] = val
        else:
            # Includes bools, None, strings, lists (all are leaf properties).
            props[key] = val

    if "compatible" in props:
        yield path, props
    for name, child in child_nodes.items():
        yield from _walk_dtc_yaml(child, f"{path}/{name}")


def _unwrap(val):
    """Unwrap dtc YAML nested lists to get the raw value.

    dtc -O yaml wraps scalars in nested lists: [[value]].
    Unwrap until we find a non-list value.
    """
    while isinstance(val, list) and len(val) == 1:
        val = val[0]
    return val


def _extract_scalar(props, prop_name: str) -> int:
    """Extract a scalar integer from a dtc YAML property."""
    val = props.get(prop_name)
    if val is None:
        return 0
    val = _unwrap(val)
    return int(val)


def _extract_reg(props, reg_idx: int, field: str) -> int:
    """Extract address or size from a 'reg' property in dtc YAML format.

    dtc YAML reg output: a list of entries, each entry is a list of cells.
    For #address-cells=2, #size-cells=2:
      [[addr_hi, addr_lo, size_hi, size_lo], ...]
    field is 'address' or 'size'.
    """
    reg_val = props.get("reg")
    if reg_val is None:
        return 0
    if not isinstance(reg_val, list) or not reg_val:
        return 0
    entry = reg_val[reg_idx] if reg_idx < len(reg_val) else reg_val[0]
    entry = _unwrap(entry)
    if not isinstance(entry, list):
        return int(entry)
    # Assume 2 address cells, 2 size cells (QEMU virt machine).
    addr_cells = 2
    size_cells = 2
    if field == "address":
        cells = entry[:addr_cells]
        result = 0
        for c in cells:
            result = (result << 32) | (int(c) & 0xFFFFFFFF)
        return result
    else:  # size
        cells = entry[addr_cells:addr_cells + size_cells]
        result = 0
        for c in cells:
            result = (result << 32) | (int(c) & 0xFFFFFFFF)
        return result


def _extract_array_value(props, prop_name: str, idx_or_key):
    """Extract a value using a simplified path expression."""
    if prop_name == "reg":
        parts = idx_or_key.split(".")
        idx = int(parts[0].strip("[]"))
        field = parts[1] if len(parts) > 1 else "address"
        return _extract_reg(props, idx, field)
    elif prop_name == "interrupts":
        idx = int(idx_or_key.strip("[]"))
        irqs = props.get("interrupts", [])
        if isinstance(irqs, list) and irqs:
            entry = _unwrap(irqs[idx] if idx < len(irqs) else irqs[0])
            if isinstance(entry, list) and entry:
                return int(entry[0])
            return int(entry)
        return 0
    else:
        return _extract_scalar(props, prop_name)


def _phandle_constructor(loader, node):
    return loader.construct_scalar(node)


def _prop_to_rust_constant(props, binding, compat):
    """Generate Rust constant lines from binding property definitions."""
    lines = []
    for prop_name, bprop in binding.get("properties", {}).items():
        rust_defs = bprop.get("rust", [])
        for rd in rust_defs:
            name = rd["name"]
            ty = rd.get("ty", "usize")
            value_path = rd.get("value", ".")
            raw = _extract_array_value(props, prop_name, value_path)
            lines.append(f"pub const {name}: {ty} = {raw:#x};")
    return lines


def _generate_device_module(props, binding, compat, instance_idx) -> str:
    """Generate a single .rs file for one device node."""
    lines = [_RUST_HEADER]
    lines.append(f"pub const COMPATIBLE: &str = \"{compat}\";\n")
    const_lines = _prop_to_rust_constant(props, binding, compat)
    lines.extend(const_lines)
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dts", required=True)
    parser.add_argument("--dts-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--cpp", default="cpp")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    if yaml is None:
        print("PyYAML is required", file=sys.stderr)
        sys.exit(1)

    # Register YAML constructor for dtc's !phandle tag.
    yaml.add_constructor("!phandle", _phandle_constructor, Loader=yaml.Loader)

    # Preprocess with CPP, then compile with dtc.
    try:
        preprocessed = _preprocess_dts(args.cpp, args.dts, args.dts_dir)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"cpp error: {e}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        ["dtc", "-I", "dts", "-O", "yaml", "-"],
        input=preprocessed,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"dtc error: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        tree = yaml.load(result.stdout, Loader=yaml.Loader)
    except Exception as e:
        print(f"YAML parse error: {e}", file=sys.stderr)
        sys.exit(1)

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

    # Walk DTS, find enabled nodes with matching bindings.
    matched = []
    compat_counts = {}

    for entry in tree if isinstance(tree, list) else [tree]:
        for _path, props in _walk_dtc_yaml(entry, ""):
            compat_raw = props.get("compatible", "")
            if isinstance(compat_raw, list):
                compat = compat_raw[0]
            elif isinstance(compat_raw, str):
                compat = compat_raw
            else:
                continue
            if not compat:
                continue
            status = props.get("status", "")
            if isinstance(status, list):
                status = status[0] if status else ""
            if status and status != "okay":
                continue
            binding = bindings.get(compat)
            if binding is None:
                continue
            idx = compat_counts.get(compat, 0)
            compat_counts[compat] = idx + 1
            matched.append((compat, props, binding, idx))

    # Generate module files.
    mod_names = []
    for compat, props, binding, idx in matched:
        mod_name = _compat_to_modname(compat)
        if idx > 0:
            mod_name = f"{mod_name}_{idx}"
        mod_names.append(mod_name)

        content = _generate_device_module(props, binding, compat, idx)
        out_path = os.path.join(args.out_dir, f"{mod_name}.rs")
        with open(out_path, "w") as f:
            f.write(content)

    # Generate mod.rs.
    mod_rs = [_RUST_HEADER]
    for mn in mod_names:
        mod_rs.append(f"pub mod {mn};")
    mod_rs.append("")

    mod_rs_path = os.path.join(args.out_dir, "mod.rs")
    with open(mod_rs_path, "w") as f:
        f.write("\n".join(mod_rs))


if __name__ == "__main__":
    main()