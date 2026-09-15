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
"""Validate a BlueOS artifact against its ELF and target contracts.

This is the build-time gate for the two ELF kinds accepted by the dynamic
loader:

  * `dynamic_app` — PIC/PIE dynamic application (ET_DYN). Requires PT_DYNAMIC
    and forbids PT_INTERP and `-static`. A self-contained PIE may have no
    DT_NEEDED entries.
  * `dso` — PIC shared object (ET_DYN). DT_SONAME is optional; an interpreter
    is forbidden.

The artifact policy and target profile are orthogonal. ``--profile`` selects
kernel/PIE/DSO rules; ``--target-profile`` selects the Thumbv7 ABI and the
exact dynamic-relocation allowlist. Other architectures deliberately have no
profile in this branch and therefore fail before packaging.

The script shells out to `llvm-readelf` (override with `LLVM_READELF`). It
reads only ELF metadata; it never executes the artifact.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# e_flags bits (ARM EABI). See the ARM ELF ABI spec, EF_ARM_*.
_EF_ARM_ABI_FLOAT_MASK = 0x600
_EF_ARM_ABI_FLOAT_SOFT = 0x200
_EF_ARM_EABIMASK = 0xFF000000
_EF_ARM_EABI_VER5 = 0x05000000

_ARM_RELOCS = frozenset({
    "R_ARM_RELATIVE",
    "R_ARM_ABS32",
    "R_ARM_GLOB_DAT",
    "R_ARM_JUMP_SLOT",
})
# This target id is board policy, not a value trusted from the input ELF.
_TARGET_PROFILES = {
    "thumbv7m-vivo-blueos-newlibeabi": {
        "class": "ELF32",
        "machine": "ARM",
        "flags_mask": 0xFF000600,
        "flags_value": _EF_ARM_EABI_VER5 | _EF_ARM_ABI_FLOAT_SOFT,
        "entry": "thumb",
        "arm_cpu": "ARM v7",
        "relocs": _ARM_RELOCS,
    },
}

_PROFILES = ("dynamic_app", "dso")


class ElfError(Exception):
    """A contract violation, raised with a human-readable reason."""


def _run(llvm_readelf, *args):
    """Run llvm-readelf and return its stdout as text, or raise ElfError."""
    cmd = [llvm_readelf, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ElfError(f"llvm-readelf not found: {llvm_readelf!r}") from None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ElfError(f"`{' '.join(cmd)}` failed: {detail}")
    return proc.stdout


def _parse_header(text):
    """Extract class/data/machine/type/flags/entry from `llvm-readelf -h`."""
    class_ = re.search(r"Class:\s+(\S+)", text)
    data = re.search(r"Data:\s+(.+)$", text, re.M)
    machine = re.search(r"Machine:\s+(\S+)", text)
    elftype = re.search(r"Type:\s+(\S+)", text)
    flags = re.search(r"Flags:\s+(0x[0-9A-Fa-f]+)", text)
    entry = re.search(r"Entry point address:\s+(0x[0-9A-Fa-f]+)", text)
    if not (class_ and data and machine and elftype and flags and entry):
        raise ElfError("unable to parse ELF header from `llvm-readelf -h`")
    return {
        "class": class_.group(1),
        "data": data.group(1).strip(),
        "machine": machine.group(1),
        "type": elftype.group(1),
        "flags": int(flags.group(1), 16),
        "entry": int(entry.group(1), 16),
    }


def _program_header_types(text):
    """Return the set of PT_* types from `llvm-readelf -l`."""
    types = set()
    # The segment table lists each segment on one line, type first.
    for match in re.finditer(r"^\s{2}(\S+)\s+0x[0-9A-Fa-f]+", text, re.M):
        types.add(match.group(1))
    return types


def _program_headers(text):
    """Return ``(type, flags)`` pairs from ``llvm-readelf -l``.

    The flag column may be rendered as either ``R E`` or ``RW``. Taking every
    token between ``MemSiz`` and ``Align`` keeps the parser independent of ELF
    class and llvm-readelf's spacing.
    """
    headers = []
    for line in text.splitlines():
        if not re.match(r"^\s{2}\S+\s+0x[0-9A-Fa-f]+", line):
            continue
        fields = line.split()
        if len(fields) < 8:
            continue
        headers.append((fields[0], "".join(fields[6:-1])))
    return headers


def _dynamic_tags(text):
    """Map dynamic tag name -> set of string values from `llvm-readelf -d`."""
    tags = {}
    for match in re.finditer(
            r"\((NEEDED|SONAME|FLAGS|FLAGS_1|TEXTREL|RPATH|"
            r"RUNPATH)\)\s+(.*)", text):
        name, value = match.group(1), match.group(2).strip()
        tags.setdefault(name, set()).add(value)
    return tags


def _relocs(text):
    """Return the set of relocation type names from `llvm-readelf -r`."""
    return set(re.findall(r"\b(R_[A-Z0-9]+_[A-Z0-9_]+)\b", text))


def _attribute_description(text, tag_name):
    """Read the `Description:` value following `TagName: <tag_name>`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(rf"TagName:\s*{tag_name}\s*$", line):
            for lookahead in lines[i + 1:i + 3]:
                m = re.search(r"Description:\s*(.+)$", lookahead)
                if m:
                    return m.group(1).strip()
    return None


def _check_target_abi(header,
                      attr_text,
                      artifact_profile,
                      target_id,
                      check_entry=True):
    """Validate metadata selected by board/package policy, never by the ELF."""
    target = _TARGET_PROFILES[target_id]
    prefix = f"{artifact_profile}/{target_id}"
    if header["class"] != target["class"]:
        raise ElfError(f"{prefix}: expected {target['class']}, "
                       f"got {header['class']}")
    if "little endian" not in header["data"].lower():
        raise ElfError(f"{prefix}: expected little-endian ELF, "
                       f"got {header['data']}")
    if header["machine"] != target["machine"]:
        raise ElfError(f"{prefix}: expected machine {target['machine']}, "
                       f"got {header['machine']}")
    flags = header["flags"]
    if flags & target["flags_mask"] != target["flags_value"]:
        raise ElfError(f"{prefix}: incompatible e_flags 0x{flags:08x} "
                       f"(mask 0x{target['flags_mask']:x}, expected "
                       f"0x{target['flags_value']:x})")

    if check_entry and target["entry"] == "thumb" and header["entry"] & 1 != 1:
        raise ElfError(f"{prefix}: entry 0x{header['entry']:x} is not Thumb")
    arm_isa = _attribute_description(attr_text, "ARM_ISA_use")
    if arm_isa is not None and arm_isa != "Not Permitted":
        raise ElfError(f"{prefix}: ARM_ISA_use is {arm_isa!r}, expected "
                       f"Thumb-only (Not Permitted)")
    cpu_arch = _attribute_description(attr_text, "CPU_arch")
    if cpu_arch != target["arm_cpu"]:
        raise ElfError(f"{prefix}: CPU_arch is {cpu_arch!r}, expected "
                       f"{target['arm_cpu']!r}")
    vfp_args = _attribute_description(attr_text, "ABI_VFP_args")
    if vfp_args == "AAPCS VFP":
        raise ElfError(
            f"{prefix}: hard-float ABI_VFP_args on soft-float target")


def _check_dynamic_relocs(profile, target_id, relocs):
    """Enforce the relocation whitelist."""
    if not relocs:
        return
    unknown = relocs - _TARGET_PROFILES[target_id]["relocs"]
    if unknown:
        raise ElfError(
            f"{profile}/{target_id}: relocation(s) outside first-class "
            f"whitelist: {', '.join(sorted(unknown))}")


def _check_load_hardening(profile, phdr_text):
    """Reject executable stacks, W+X loads, and native ELF TLS."""
    headers = _program_headers(phdr_text)
    for kind, flags in headers:
        if kind == "LOAD" and "W" in flags and "E" in flags:
            raise ElfError(f"{profile}: writable executable PT_LOAD present")
        if kind == "GNU_STACK" and "E" in flags:
            raise ElfError(f"{profile}: executable GNU_STACK present")
    if any(kind == "TLS" for kind, _ in headers):
        raise ElfError(
            f"{profile}: PT_TLS present (current implementation uses emutls)")


def _check_dynamic_hardening(profile, phdr_text, tags, note_text):
    """Enforce the common NOW/textrel/search/build-id dynamic contract.

    PT_GNU_RELRO is deliberately *not* required. GNU ld's armelf emulation --
    what `arm-none-eabi-gcc` selects on the Thumb boards -- emits no RELRO
    segment, so demanding one would reject every artifact linked with the
    toolchain's own `ld`. The loader already treats RELRO as optional
    (`validate_relro` accepts `None`), so an image without it still loads;
    what must not happen is a dynamic relocation in a segment that can never
    be written, which `DT_TEXTREL` below still catches.
    """
    phdrs = _program_header_types(phdr_text)
    if "GNU_RELRO" not in phdrs:
        print(
            f"{profile}: note: no PT_GNU_RELRO "
            f"(GNU ld's armelf emulation emits none)",
            file=sys.stderr)
    for forbidden in ("TEXTREL", "RPATH", "RUNPATH"):
        if forbidden in tags:
            raise ElfError(f"{profile}: DT_{forbidden} present")
    now = any("BIND_NOW" in value for value in tags.get("FLAGS", set()))
    now = now or any("NOW" in value for value in tags.get("FLAGS_1", set()))
    if not now:
        raise ElfError(f"{profile}: NOW binding missing")
    if not re.search(r"\bBuild ID:\s*[0-9A-Fa-f]+", note_text):
        raise ElfError(f"{profile}: GNU build-id missing")


def _check_dynamic_app(llvm_readelf, elf, target_id):
    header_text = _run(llvm_readelf, "-h", elf)
    phdr_text = _run(llvm_readelf, "-l", elf)
    dyn_text = _run(llvm_readelf, "-d", elf)
    reloc_text = _run(llvm_readelf, "-r", elf)
    attr_text = _run(llvm_readelf, "-A", elf)
    note_text = _run(llvm_readelf, "-n", elf)

    header = _parse_header(header_text)
    _check_target_abi(header, attr_text, "dynamic_app", target_id)
    _check_load_hardening("dynamic_app", phdr_text)

    if header["type"] != "DYN":
        raise ElfError(f"dynamic_app: expected ET_DYN, got {header['type']}")
    phdrs = _program_header_types(phdr_text)
    if "INTERP" in phdrs:
        raise ElfError("dynamic_app: PT_INTERP present (no dynamic linker)")
    if "DYNAMIC" not in phdrs:
        raise ElfError("dynamic_app: PT_DYNAMIC missing")

    tags = _dynamic_tags(dyn_text)
    flags1 = tags.get("FLAGS_1", set())
    if any("STATIC" in f for f in flags1):
        raise ElfError("dynamic_app: linked -static (DF_1_STATIC present)")
    if not any("PIE" in f for f in flags1):
        raise ElfError("dynamic_app: not PIE (DF_1_PIE absent)")
    _check_dynamic_hardening("dynamic_app", phdr_text, tags, note_text)

    _check_dynamic_relocs("dynamic_app", target_id, _relocs(reloc_text))


def _check_dso(llvm_readelf, elf, target_id):
    header_text = _run(llvm_readelf, "-h", elf)
    phdr_text = _run(llvm_readelf, "-l", elf)
    dyn_text = _run(llvm_readelf, "-d", elf)
    reloc_text = _run(llvm_readelf, "-r", elf)
    attr_text = _run(llvm_readelf, "-A", elf)
    note_text = _run(llvm_readelf, "-n", elf)

    header = _parse_header(header_text)
    _check_target_abi(header, attr_text, "dso", target_id, check_entry=False)
    _check_load_hardening("dso", phdr_text)

    if header["type"] != "DYN":
        raise ElfError(f"dso: expected ET_DYN, got {header['type']}")
    phdrs = _program_header_types(phdr_text)
    if "INTERP" in phdrs:
        raise ElfError("dso: PT_INTERP present")
    if "DYNAMIC" not in phdrs:
        raise ElfError("dso: PT_DYNAMIC missing")

    tags = _dynamic_tags(dyn_text)
    _check_dynamic_hardening("dso", phdr_text, tags, note_text)

    _check_dynamic_relocs("dso", target_id, _relocs(reloc_text))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate a BlueOS artifact against its ELF contract.")
    parser.add_argument("--profile",
                        required=True,
                        choices=_PROFILES,
                        help="artifact link policy to validate")
    parser.add_argument("--target-profile",
                        required=True,
                        choices=tuple(_TARGET_PROFILES),
                        help="board/package-selected target ABI profile")
    parser.add_argument("elf", help="path to the ELF artifact")
    parser.add_argument("--llvm-readelf",
                        default=os.environ.get("LLVM_READELF", "llvm-readelf"),
                        help="path to llvm-readelf (default: $LLVM_READELF or "
                        "llvm-readelf)")
    parser.add_argument(
        "--stamp",
        default=None,
        help="touch this GN action output after a successful check")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.elf):
        print(f"FAIL {args.profile}: {args.elf}: file not found",
              file=sys.stderr)
        return 1
    try:
        if args.profile == "dso":
            _check_dso(args.llvm_readelf, args.elf, args.target_profile)
        else:
            _check_dynamic_app(args.llvm_readelf, args.elf,
                               args.target_profile)
    except ElfError as error:
        print(f"FAIL {args.profile}: {args.elf}: {error}", file=sys.stderr)
        return 1

    if args.stamp is not None:
        stamp = Path(args.stamp)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    print(f"PASS {args.profile}/{args.target_profile}: {args.elf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
