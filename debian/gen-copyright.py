#!/usr/bin/env python3
"""Generate debian/copyright from the vendored Rust crates.

Parses each vendor/<crate>/Cargo.toml for license and copyright information,
then emits a DEP-5 machine-readable copyright file covering both the upstream
cloud-hypervisor source and all vendored dependencies.

Usage:
    debian/gen-copyright.py [--vendor-dir DIR] > debian/copyright

If --vendor-dir is not specified, defaults to "vendor/" relative to the
repository root (the parent of the directory containing this script).
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


# SPDX license identifiers that can be referenced from
# /usr/share/common-licenses/ on Debian systems.  Only identifiers whose
# SPDX short-name matches the filename under /usr/share/common-licenses/
# belong here.  Suffixed variants like "-or-later" or "-only" need custom
# handlers below to point at the correct filename.
COMMON_LICENSES = {
    "Apache-2.0",
    "GPL-2.0",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "LGPL-2.1",
    "LGPL-2.1-only",
    "LGPL-3.0",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
}

# Static preamble: upstream cloud-hypervisor and debian/* paragraphs.
HEADER = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: cloud-hypervisor
Upstream-Contact: https://github.com/cloud-hypervisor/cloud-hypervisor
Source: https://github.com/cloud-hypervisor/cloud-hypervisor

Files: *
Copyright: Cloud Hypervisor Authors
License: Apache-2.0 OR BSD-3-Clause

Files: debian/*
Copyright: 2026 Gauthier Jolly <gauthier.jolly@canonical.com>
License: Apache-2.0
"""


def parse_toml_string(value):
    """Minimal TOML string value parser (handles quoted strings)."""
    value = value.strip()
    if value.startswith('"'):
        # Simple quoted string (no escape handling needed for these fields)
        return value.strip('"')
    return value


def parse_toml_array(lines, start_idx):
    """Parse a TOML array starting at start_idx, return (items, next_idx)."""
    items = []
    i = start_idx
    # Check if it's an inline array on one line
    first_line = lines[i]
    match = re.search(r"\[(.+)\]", first_line)
    if match:
        # Inline array
        content = match.group(1)
        for item in content.split(","):
            item = item.strip().strip('"')
            if item:
                items.append(item)
        return items, i + 1

    # Multi-line array
    i += 1
    while i < len(lines):
        line = lines[i].strip()
        if line == "]":
            return items, i + 1
        # Strip trailing comma and quotes
        item = line.rstrip(",").strip().strip('"')
        if item:
            items.append(item)
        i += 1
    return items, i


def parse_cargo_toml(path):
    """Extract name, version, authors, and license from a Cargo.toml."""
    info = {
        "name": None,
        "version": None,
        "authors": [],
        "license": None,
    }

    lines = path.read_text(encoding="utf-8").splitlines()
    in_package = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track sections
        if stripped.startswith("["):
            in_package = stripped == "[package]"
            i += 1
            continue

        if not in_package:
            i += 1
            continue

        if "=" not in stripped:
            i += 1
            continue

        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()

        if key == "name":
            info["name"] = parse_toml_string(value)
        elif key == "version":
            info["version"] = parse_toml_string(value)
        elif key == "license":
            info["license"] = parse_toml_string(value)
        elif key == "authors":
            if value.startswith("["):
                items, next_i = parse_toml_array(lines, i)
                info["authors"] = items
                i = next_i
                continue
        i += 1

    return info


def extract_copyright_from_license(vendor_dir, crate_dir):
    """Try to extract copyright lines from LICENSE files."""
    copyrights = []
    for license_file in sorted(crate_dir.iterdir()):
        if not license_file.is_file():
            continue
        name_upper = license_file.name.upper()
        if not (name_upper.startswith("LICENSE") or name_upper.startswith("COPYING")):
            continue
        try:
            text = license_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines()[:30]:
            # Look for "Copyright (c) ..." or "Copyright YEAR ..." lines
            m = re.match(
                r"^\s*Copyright\s+(?:\(c\)\s*)?(.+)",
                line,
                re.IGNORECASE,
            )
            if m:
                holder = m.group(1).strip().rstrip(".")
                # Skip placeholder entries and entries that are just
                # year ranges without an actual name (e.g. "2016--2023")
                if not holder:
                    continue
                if re.match(r"^[\d\s,\-]+$", holder):
                    continue
                if "[yyyy]" in holder or "[name" in holder:
                    continue
                if holder not in copyrights:
                    copyrights.append(holder)
    return copyrights


def normalize_license(license_str):
    """Normalize license expression for grouping.

    Converts '/' separators to ' OR ' (older Cargo convention) and
    canonicalizes simple OR-only expressions by sorting the alternatives
    alphabetically so that e.g. "MIT OR Apache-2.0" and "Apache-2.0 OR MIT"
    become the same string.

    SPDX 'WITH' exceptions (e.g. "Apache-2.0 WITH LLVM-exception") are
    collapsed into a single hyphenated identifier (e.g.
    "Apache-2.0-with-LLVM-exception") because DEP-5 does not recognize the
    'WITH' keyword.

    Compound expressions that mix AND or parentheses are left as-is
    (only whitespace and '/' normalization is applied).
    """
    # Replace '/' with ' OR ' (old-style Cargo.toml convention)
    normalized = license_str.replace("/", " OR ")
    # Normalize whitespace
    normalized = " ".join(normalized.split())

    # Collapse SPDX "X WITH Y" into "X-with-Y" for DEP-5 compatibility.
    normalized = re.sub(
        r"(\S+)\s+WITH\s+(\S+)",
        r"\1-with-\2",
        normalized,
    )

    # If the expression is a simple OR-only list (no AND or parens),
    # sort the alternatives for canonical ordering.
    if " AND " not in normalized and "(" not in normalized:
        parts = [p.strip() for p in normalized.split(" OR ")]
        normalized = " OR ".join(sorted(parts))

    return normalized


def format_dep5_license(license_expr):
    """Format license expression for DEP-5 output."""
    return normalize_license(license_expr)


def license_paragraph(license_expr):
    """Generate a License: paragraph body for a given SPDX expression."""
    # Split on OR/AND to find individual licenses
    tokens = re.split(r"\s+(?:OR|AND)\s+", license_expr)
    parts = []
    for token in tokens:
        token = token.strip("() ")
        if token in COMMON_LICENSES:
            parts.append(
                f" On Debian systems, the complete text of the {token}\n"
                f" license can be found in "
                f'"/usr/share/common-licenses/{token}".'
            )
        elif token == "MIT":
            parts.append(
                " Permission is hereby granted, free of charge, to any person"
                " obtaining\n"
                " a copy of this software and associated documentation files"
                ' (the\n "Software"), to deal in the Software without'
                " restriction, including\n"
                " without limitation the rights to use, copy, modify, merge,"
                " publish,\n"
                " distribute, sublicense, and/or sell copies of the Software,"
                " and to\n"
                " permit persons to whom the Software is furnished to do so,"
                " subject to\n"
                " the following conditions:\n"
                " .\n"
                " The above copyright notice and this permission notice shall"
                " be\n"
                " included in all copies or substantial portions of the"
                " Software.\n"
                " .\n"
                ' THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY'
                " KIND,\n"
                " EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE"
                " WARRANTIES OF\n"
                " MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND"
                " NONINFRINGEMENT.\n"
                " IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE"
                " LIABLE FOR ANY\n"
                " CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF"
                " CONTRACT,\n"
                " TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION"
                " WITH THE\n"
                " SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE."
            )
        elif token == "BSD-3-Clause":
            parts.append(
                " Redistribution and use in source and binary forms, with or"
                " without\n"
                " modification, are permitted provided that the following"
                " conditions are met:\n"
                " .\n"
                " 1. Redistributions of source code must retain the above"
                " copyright notice,\n"
                "    this list of conditions and the following disclaimer.\n"
                " .\n"
                " 2. Redistributions in binary form must reproduce the above"
                " copyright notice,\n"
                "    this list of conditions and the following disclaimer in"
                " the documentation\n"
                "    and/or other materials provided with the distribution.\n"
                " .\n"
                " 3. Neither the name of the copyright holder nor the names of"
                " its contributors\n"
                "    may be used to endorse or promote products derived from"
                " this software\n"
                "    without specific prior written permission.\n"
                " .\n"
                " THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND"
                " CONTRIBUTORS"
                ' "AS IS"\n'
                " AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT"
                " LIMITED TO, THE\n"
                " IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A"
                " PARTICULAR PURPOSE\n"
                " ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR"
                " CONTRIBUTORS BE\n"
                " LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,"
                " EXEMPLARY, OR\n"
                " CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,"
                " PROCUREMENT OF\n"
                " SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR"
                " PROFITS; OR BUSINESS\n"
                " INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF"
                " LIABILITY, WHETHER IN\n"
                " CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR"
                " OTHERWISE)\n"
                " ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF"
                " ADVISED OF THE\n"
                " POSSIBILITY OF SUCH DAMAGE."
            )
        elif token == "BSD-2-Clause":
            parts.append(
                " Redistribution and use in source and binary forms, with or"
                " without\n"
                " modification, are permitted provided that the following"
                " conditions are met:\n"
                " .\n"
                " 1. Redistributions of source code must retain the above"
                " copyright notice,\n"
                "    this list of conditions and the following disclaimer.\n"
                " .\n"
                " 2. Redistributions in binary form must reproduce the above"
                " copyright notice,\n"
                "    this list of conditions and the following disclaimer in"
                " the documentation\n"
                "    and/or other materials provided with the distribution.\n"
                " .\n"
                " THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND"
                " CONTRIBUTORS"
                ' "AS IS"\n'
                " AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT"
                " LIMITED TO, THE\n"
                " IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A"
                " PARTICULAR PURPOSE\n"
                " ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR"
                " CONTRIBUTORS BE\n"
                " LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,"
                " EXEMPLARY, OR\n"
                " CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,"
                " PROCUREMENT OF\n"
                " SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR"
                " PROFITS; OR BUSINESS\n"
                " INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF"
                " LIABILITY, WHETHER IN\n"
                " CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR"
                " OTHERWISE)\n"
                " ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF"
                " ADVISED OF THE\n"
                " POSSIBILITY OF SUCH DAMAGE."
            )
        elif token == "MPL-2.0":
            parts.append(
                " On Debian systems, the complete text of the Mozilla Public"
                " License,\n"
                " Version 2.0 can be found in"
                ' "/usr/share/common-licenses/MPL-2.0".'
            )
        elif token == "Unlicense":
            parts.append(
                " This is free and unencumbered software released into the"
                " public domain.\n"
                " .\n"
                " Anyone is free to copy, modify, publish, use, compile, sell,"
                " or\n"
                " distribute this software, either in source code form or as a"
                " compiled\n"
                " binary, for any purpose, commercial or non-commercial, and"
                " by any means.\n"
                " .\n"
                " For more information, please refer to"
                " <https://unlicense.org/>."
            )
        elif token == "0BSD":
            parts.append(
                " Permission to use, copy, modify, and/or distribute this"
                " software for\n"
                " any purpose with or without fee is hereby granted.\n"
                " .\n"
                ' THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS'
                " ALL WARRANTIES\n"
                " WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED"
                " WARRANTIES OF\n"
                " MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE"
                " LIABLE FOR\n"
                " ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR"
                " ANY DAMAGES\n"
                " WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,"
                " WHETHER IN AN\n"
                " ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION,"
                " ARISING OUT OF\n"
                " OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS"
                " SOFTWARE."
            )
        elif token == "Zlib":
            parts.append(
                " This software is provided 'as-is', without any express or"
                " implied\n"
                " warranty. In no event will the authors be held liable for"
                " any damages\n"
                " arising from the use of this software.\n"
                " .\n"
                " Permission is granted to anyone to use this software for any"
                " purpose,\n"
                " including commercial applications, and to alter it and"
                " redistribute it\n"
                " freely, subject to the following restrictions:\n"
                " .\n"
                " 1. The origin of this software must not be misrepresented;"
                " you must not\n"
                "    claim that you wrote the original software. If you use"
                " this software\n"
                "    in a product, an acknowledgment in the product"
                " documentation would be\n"
                "    appreciated but is not required.\n"
                " 2. Altered source versions must be plainly marked as such,"
                " and must not be\n"
                "    misrepresented as being the original software.\n"
                " 3. This notice may not be removed or altered from any source"
                " distribution."
            )
        elif token == "Unicode-3.0":
            parts.append(
                " UNICODE LICENSE V3\n"
                " .\n"
                " Distributed under the Terms of Use in\n"
                " https://www.unicode.org/copyright.html."
            )
        elif token == "Apache-2.0-with-LLVM-exception":
            parts.append(
                " On Debian systems, the complete text of the Apache-2.0\n"
                " license can be found in"
                ' "/usr/share/common-licenses/Apache-2.0".\n'
                " .\n"
                " --- LLVM Exception ---\n"
                " .\n"
                " As an exception, if, as a result of your compiling your"
                " source code,\n"
                " portions of this Software are included in a machine-"
                "executable object\n"
                " form of such source code, you may redistribute such"
                " machine-executable\n"
                " object code without including the full text of the"
                " license."
            )
        elif token == "LGPL-2.1-or-later":
            parts.append(
                " On Debian systems, the complete text of the GNU Lesser"
                " General Public\n"
                " License, Version 2.1 can be found in"
                ' "/usr/share/common-licenses/LGPL-2.1".'
            )
        else:
            parts.append(
                f" See the license text in the corresponding crate directory"
                f" under vendor/."
            )
    return "\n .\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Generate debian/copyright from vendored Rust crates."
    )
    parser.add_argument(
        "--vendor-dir",
        type=Path,
        default=None,
        help="Path to the vendor directory (default: <repo>/vendor/)",
    )
    args = parser.parse_args()

    # Determine repo root (parent of debian/)
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    vendor_dir = args.vendor_dir or repo_root / "vendor"
    if not vendor_dir.is_dir():
        print(
            f"Error: vendor directory not found: {vendor_dir}\n"
            "Run 'cargo vendor' first or pass --vendor-dir.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Collect crate info
    crates = []
    for crate_dir in sorted(vendor_dir.iterdir()):
        if not crate_dir.is_dir():
            continue
        cargo_toml = crate_dir / "Cargo.toml"
        if not cargo_toml.exists():
            continue

        info = parse_cargo_toml(cargo_toml)
        if not info["license"]:
            print(
                f"Warning: no license field in {cargo_toml}, skipping",
                file=sys.stderr,
            )
            continue

        # Determine copyright holders
        if info["authors"]:
            # Strip email addresses for cleaner copyright lines
            copyrights = []
            for author in info["authors"]:
                # Keep the full author string (name <email>) as-is
                copyrights.append(author)
        else:
            # Try to extract from LICENSE files
            copyrights = extract_copyright_from_license(vendor_dir, crate_dir)

        if not copyrights:
            crate_name = info["name"] or crate_dir.name
            copyrights = [f"{crate_name} contributors"]

        info["copyrights"] = copyrights
        info["dir_name"] = crate_dir.name
        crates.append(info)

    # Group crates by (normalized license, frozen copyright tuple) for
    # compact output. Each unique combination gets its own Files: paragraph.
    # Key: (normalized_license, tuple of copyright holders)
    # Value: list of directory names
    groups = defaultdict(list)
    for crate in crates:
        key = (
            normalize_license(crate["license"]),
            tuple(crate["copyrights"]),
        )
        groups[key].append(crate["dir_name"])

    # Collect all unique license expressions we need to document.
    # Include the static header licenses so they are not duplicated.
    all_licenses = {"Apache-2.0", "Apache-2.0 OR BSD-3-Clause", "BSD-3-Clause"}
    for (license_expr, _), _ in groups.items():
        all_licenses.add(license_expr)
        # DEP-5 / lintian requires standalone License: paragraphs for each
        # individual component of a compound (OR/AND) expression.
        for token in re.split(r"\s+(?:OR|AND)\s+", license_expr):
            token = token.strip("() ")
            if token:
                all_licenses.add(token)

    # Output
    output = []
    output.append(HEADER)

    # Emit Files: paragraphs sorted by license then by first crate dir
    for (license_expr, copyrights), dirs in sorted(
        groups.items(), key=lambda x: (x[0][0], x[1])
    ):
        # DEP-5 Files: field uses space-separated globs; continuation
        # lines are indented with a single space.  Wrap at ~76 columns
        # for readability.
        sorted_dirs = sorted(dirs)
        globs = [f"vendor/{d}/*" for d in sorted_dirs]
        files_lines = []
        current_line = "Files:"
        for g in globs:
            if len(current_line) + 1 + len(g) > 76 and current_line != "Files:":
                files_lines.append(current_line)
                current_line = " " + g
            else:
                current_line += " " + g
        files_lines.append(current_line)
        files_field = "\n".join(files_lines)

        copyright_lines = "\n           ".join(copyrights)
        dep5_license = format_dep5_license(license_expr)
        output.append(
            f"{files_field}\n"
            f"Copyright: {copyright_lines}\n"
            f"License: {dep5_license}\n"
        )

    # Emit License: paragraphs for all unique licenses (sorted, no dupes)
    for license_expr in sorted(all_licenses):
        dep5_license = format_dep5_license(license_expr)
        body = license_paragraph(dep5_license)
        output.append(f"License: {dep5_license}\n{body}\n")

    # Each entry in output ends with "\n" forming a paragraph; join with
    # "\n" to get the blank line between paragraphs.  Strip any trailing
    # whitespace to get a clean file ending with a single newline.
    print("\n".join(output).rstrip() + "\n", end="")


if __name__ == "__main__":
    main()
