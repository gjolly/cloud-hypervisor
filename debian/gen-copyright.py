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
# Per-file license overrides for files whose actual license differs from
# the crate-level Cargo.toml license field.  These are emitted after the
# crate-level paragraphs so that DEP-5 last-match-wins semantics apply.
FILE_OVERRIDES = [
    {
        "files": "vendor/zstd-sys/src/bindings_*",
        "copyright": "Meta Platforms, Inc. and affiliates",
        "license": "BSD-3-Clause",
    },
]

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

CC_BY_40_TEXT = (
    """\
 Attribution 4.0 International
 .
 =======================================================================
 .
 Creative Commons Corporation ("Creative Commons") is not a law firm and
 does not provide legal services or legal advice. Distribution of
 Creative Commons public licenses does not create a lawyer-client or
 other relationship. Creative Commons makes its licenses and related
 information available on an "as-is" basis. Creative Commons gives no
 warranties regarding its licenses, any material licensed under their
 terms and conditions, or any related information. Creative Commons
 disclaims all liability for damages resulting from their use to the
 fullest extent possible.
 .
 Using Creative Commons Public Licenses
 .
 Creative Commons public licenses provide a standard set of terms and
 conditions that creators and other rights holders may use to share
 original works of authorship and other material subject to copyright
 and certain other rights specified in the public license below. The
 following considerations are for informational purposes only, are not
 exhaustive, and do not form part of our licenses.
 .
      Considerations for licensors: Our public licenses are
      intended for use by those authorized to give the public
      permission to use material in ways otherwise restricted by
      copyright and certain other rights. Our licenses are
      irrevocable. Licensors should read and understand the terms
      and conditions of the license they choose before applying it.
      Licensors should also secure all rights necessary before
      applying our licenses so that the public can reuse the
      material as expected. Licensors should clearly mark any
      material not subject to the license. This includes other CC-
      licensed material, or material used under an exception or
      limitation to copyright. More considerations for licensors:
     wiki.creativecommons.org/Considerations_for_licensors
 .
      Considerations for the public: By using one of our public
      licenses, a licensor grants the public permission to use the
      licensed material under specified terms and conditions. If
      the licensor's permission is not necessary for any reason--for
      example, because of any applicable exception or limitation to
      copyright--then that use is not regulated by the license. Our
      licenses grant only permissions under copyright and certain
      other rights that a licensor has authority to grant. Use of
      the licensed material may still be restricted for other
      reasons, including because others have copyright or other
      rights in the material. A licensor may make special requests,
      such as asking that all changes be marked or described.
      Although not required by our licenses, you are encouraged to
      respect those requests where reasonable. More considerations
      for the public:
     wiki.creativecommons.org/Considerations_for_licensees
 .
 =======================================================================
 .
 Creative Commons Attribution 4.0 International Public License
 .
 By exercising the Licensed Rights (defined below), You accept and agree
 to be bound by the terms and conditions of this Creative Commons
 Attribution 4.0 International Public License ("Public License"). To the
 extent this Public License may be interpreted as a contract, You are
 granted the Licensed Rights in consideration of Your acceptance of
 these terms and conditions, and the Licensor grants You such rights in
 consideration of benefits the Licensor receives from making the
 Licensed Material available under these terms and conditions.
 .
 .
 Section 1 -- Definitions.
 .
   a. Adapted Material means material subject to Copyright and Similar
      Rights that is derived from or based upon the Licensed Material
      and in which the Licensed Material is translated, altered,
      arranged, transformed, or otherwise modified in a manner requiring
      permission under the Copyright and Similar Rights held by the
      Licensor. For purposes of this Public License, where the Licensed
      Material is a musical work, performance, or sound recording,
      Adapted Material is always produced where the Licensed Material is
      synched in timed relation with a moving image.
 .
   b. Adapter's License means the license You apply to Your Copyright
      and Similar Rights in Your contributions to Adapted Material in
      accordance with the terms and conditions of this Public License.
 .
   c. Copyright and Similar Rights means copyright and/or similar rights
      closely related to copyright including, without limitation,
      performance, broadcast, sound recording, and Sui Generis Database
      Rights, without regard to how the rights are labeled or
      categorized. For purposes of this Public License, the rights
      specified in Section 2(b)(1)-(2) are not Copyright and Similar
      Rights.
 .
   d. Effective Technological Measures means those measures that, in the
      absence of proper authority, may not be circumvented under laws
      fulfilling obligations under Article 11 of the WIPO Copyright
      Treaty adopted on December 20, 1996, and/or similar international
      agreements.
 .
   e. Exceptions and Limitations means fair use, fair dealing, and/or
      any other exception or limitation to Copyright and Similar Rights
      that applies to Your use of the Licensed Material.
 .
   f. Licensed Material means the artistic or literary work, database,
      or other material to which the Licensor applied this Public
      License.
 .
   g. Licensed Rights means the rights granted to You subject to the
      terms and conditions of this Public License, which are limited to
      all Copyright and Similar Rights that apply to Your use of the
      Licensed Material and that the Licensor has authority to license.
 .
   h. Licensor means the individual(s) or entity(ies) granting rights
      under this Public License.
 .
   i. Share means to provide material to the public by any means or
      process that requires permission under the Licensed Rights, such
      as reproduction, public display, public performance, distribution,
      dissemination, communication, or importation, and to make material
      available to the public including in ways that members of the
      public may access the material from a place and at a time
      individually chosen by them.
 .
   j. Sui Generis Database Rights means rights other than copyright
      resulting from Directive 96/9/EC of the European Parliament and of
      the Council of 11 March 1996 on the legal protection of databases,
      as amended and/or succeeded, as well as other essentially
      equivalent rights anywhere in the world.
 .
   k. You means the individual or entity exercising the Licensed Rights
      under this Public License. Your has a corresponding meaning.
 .
 .
 Section 2 -- Scope.
 .
   a. License grant.
 .
        1. Subject to the terms and conditions of this Public License,
           the Licensor hereby grants You a worldwide, royalty-free,
           non-sublicensable, non-exclusive, irrevocable license to
           exercise the Licensed Rights in the Licensed Material to:
 .
             a. reproduce and Share the Licensed Material, in whole or
                in part; and
 .
             b. produce, reproduce, and Share Adapted Material.
 .
        2. Exceptions and Limitations. For the avoidance of doubt, where
           Exceptions and Limitations apply to Your use, this Public
           License does not apply, and You do not need to comply with
           its terms and conditions.
 .
        3. Term. The term of this Public License is specified in Section
           6(a).
 .
        4. Media and formats; technical modifications allowed. The
           Licensor authorizes You to exercise the Licensed Rights in
           all media and formats whether now known or hereafter created,
           and to make technical modifications necessary to do so. The
           Licensor waives and/or agrees not to assert any right or
           authority to forbid You from making technical modifications
           necessary to exercise the Licensed Rights, including
           technical modifications necessary to circumvent Effective
           Technological Measures. For purposes of this Public License,
           simply making modifications authorized by this Section 2(a)
           (4) never produces Adapted Material.
 .
        5. Downstream recipients.
 .
             a. Offer from the Licensor -- Licensed Material. Every
                recipient of the Licensed Material automatically
                receives an offer from the Licensor to exercise the
                Licensed Rights under the terms and conditions of this
                Public License.
 .
             b. No downstream restrictions. You may not offer or impose
                any additional or different terms or conditions on, or
                apply any Effective Technological Measures to, the
                Licensed Material if doing so restricts exercise of the
                Licensed Rights by any recipient of the Licensed
                Material.
 .
        6. No endorsement. Nothing in this Public License constitutes or
           may be construed as permission to assert or imply that You
           are, or that Your use of the Licensed Material is, connected
           with, or sponsored, endorsed, or granted official status by,
           the Licensor or others designated to receive attribution as
           provided in Section 3(a)(1)(A)(i).
 .
   b. Other rights.
 .
        1. Moral rights, such as the right of integrity, are not
           licensed under this Public License, nor are publicity,
           privacy, and/or other similar personality rights; however, to
           the extent possible, the Licensor waives and/or agrees not to
           assert any such rights held by the Licensor to the limited
           extent necessary to allow You to exercise the Licensed
           Rights, but not otherwise.
 .
        2. Patent and trademark rights are not licensed under this
           Public License.
 .
        3. To the extent possible, the Licensor waives any right to
           collect royalties from You for the exercise of the Licensed
           Rights, whether directly or through a collecting society
           under any voluntary or waivable statutory or compulsory
           licensing scheme. In all other cases the Licensor expressly
           reserves any right to collect such royalties.
 .
 .
 Section 3 -- License Conditions.
 .
 Your exercise of the Licensed Rights is expressly made subject to the
 following conditions.
 .
   a. Attribution.
 .
        1. If You Share the Licensed Material (including in modified
           form), You must:
 .
             a. retain the following if it is supplied by the Licensor
                with the Licensed Material:
 .
                  i. identification of the creator(s) of the Licensed
                     Material and any others designated to receive
                     attribution, in any reasonable manner requested by
                     the Licensor (including by pseudonym if
                     designated);
 .
                 ii. a copyright notice;
 .
                iii. a notice that refers to this Public License;
 .
                 iv. a notice that refers to the disclaimer of
                     warranties;
 .
                  v. a URI or hyperlink to the Licensed Material to the
                     extent reasonably practicable;
 .
             b. indicate if You modified the Licensed Material and
                retain an indication of any previous modifications; and
 .
             c. indicate the Licensed Material is licensed under this
                Public License, and include the text of, or the URI or
                hyperlink to, this Public License.
 .
        2. You may satisfy the conditions in Section 3(a)(1) in any
           reasonable manner based on the medium, means, and context in
           which You Share the Licensed Material. For example, it may be
           reasonable to satisfy the conditions by providing a URI or
           hyperlink to a resource that includes the required
           information.
 .
        3. If requested by the Licensor, You must remove any of the
           information required by Section 3(a)(1)(A) to the extent
           reasonably practicable.
 .
        4. If You Share Adapted Material You produce, the Adapter's
           License You apply must not prevent recipients of the Adapted
           Material from complying with this Public License.
 .
 .
 Section 4 -- Sui Generis Database Rights.
 .
 Where the Licensed Rights include Sui Generis Database Rights that
 apply to Your use of the Licensed Material:
 .
   a. for the avoidance of doubt, Section 2(a)(1) grants You the right
      to extract, reuse, reproduce, and Share all or a substantial
      portion of the contents of the database;
 .
   b. if You include all or a substantial portion of the database
      contents in a database in which You have Sui Generis Database
      Rights, then the database in which You have Sui Generis Database
      Rights (but not its individual contents) is Adapted Material; and
 .
   c. You must comply with the conditions in Section 3(a) if You Share
      all or a substantial portion of the contents of the database.
 .
 For the avoidance of doubt, this Section 4 supplements and does not
 replace Your obligations under this Public License where the Licensed
 Rights include other Copyright and Similar Rights.
 .
 .
 Section 5 -- Disclaimer of Warranties and Limitation of Liability.
 .
   a. UNLESS OTHERWISE SEPARATELY UNDERTAKEN BY THE LICENSOR, TO THE
      EXTENT POSSIBLE, THE LICENSOR OFFERS THE LICENSED MATERIAL AS-IS
      AND AS-AVAILABLE, AND MAKES NO REPRESENTATIONS OR WARRANTIES OF
      ANY KIND CONCERNING THE LICENSED MATERIAL, WHETHER EXPRESS,
      IMPLIED, STATUTORY, OR OTHER. THIS INCLUDES, WITHOUT LIMITATION,
      WARRANTIES OF TITLE, MERCHANTABILITY, FITNESS FOR A PARTICULAR
      PURPOSE, NON-INFRINGEMENT, ABSENCE OF LATENT OR OTHER DEFECTS,
      ACCURACY, OR THE PRESENCE OR ABSENCE OF ERRORS, WHETHER OR NOT
      KNOWN OR DISCOVERABLE. WHERE DISCLAIMERS OF WARRANTIES ARE NOT
      ALLOWED IN FULL OR IN PART, THIS DISCLAIMER MAY NOT APPLY TO YOU.
 .
   b. TO THE EXTENT POSSIBLE, IN NO EVENT WILL THE LICENSOR BE LIABLE
      TO YOU ON ANY LEGAL THEORY (INCLUDING, WITHOUT LIMITATION,
      NEGLIGENCE) OR OTHERWISE FOR ANY DIRECT, SPECIAL, INDIRECT,
      INCIDENTAL, CONSEQUENTIAL, PUNITIVE, EXEMPLARY, OR OTHER LOSSES,
      COSTS, EXPENSES, OR DAMAGES ARISING OUT OF THIS PUBLIC LICENSE OR
      USE OF THE LICENSED MATERIAL, EVEN IF THE LICENSOR HAS BEEN
      ADVISED OF THE POSSIBILITY OF SUCH LOSSES, COSTS, EXPENSES, OR
      DAMAGES. WHERE A LIMITATION OF LIABILITY IS NOT ALLOWED IN FULL OR
      IN PART, THIS LIMITATION MAY NOT APPLY TO YOU.
 .
   c. The disclaimer of warranties and limitation of liability provided
      above shall be interpreted in a manner that, to the extent
      possible, most closely approximates an absolute disclaimer and
      waiver of all liability.
 .
 .
 Section 6 -- Term and Termination.
 .
   a. This Public License applies for the term of the Copyright and
      Similar Rights licensed here. However, if You fail to comply with
      this Public License, then Your rights under this Public License
      terminate automatically.
 .
   b. Where Your right to use the Licensed Material has terminated under
      Section 6(a), it reinstates:
 .
        1. automatically as of the date the violation is cured, provided
           it is cured within 30 days of Your discovery of the
           violation; or
 .
        2. upon express reinstatement by the Licensor.
 .
      For the avoidance of doubt, this Section 6(b) does not affect any
      right the Licensor may have to seek remedies for Your violations
      of this Public License.
 .
   c. For the avoidance of doubt, the Licensor may also offer the
      Licensed Material under separate terms or conditions or stop
      distributing the Licensed Material at any time; however, doing so
      will not terminate this Public License.
 .
   d. Sections 1, 5, 6, 7, and 8 survive termination of this Public
      License.
 .
 .
 Section 7 -- Other Terms and Conditions.
 .
   a. The Licensor shall not be bound by any additional or different
      terms or conditions communicated by You unless expressly agreed.
 .
   b. Any arrangements, understandings, or agreements regarding the
      Licensed Material not stated herein are separate from and
      independent of the terms and conditions of this Public License.
 .
 .
 Section 8 -- Interpretation.
 .
   a. For the avoidance of doubt, this Public License does not, and
      shall not be interpreted to, reduce, limit, restrict, or impose
      conditions on any use of the Licensed Material that could lawfully
      be made without permission under this Public License.
 .
   b. To the extent possible, if any provision of this Public License is
      deemed unenforceable, it shall be automatically reformed to the
      minimum extent necessary to make it enforceable. If the provision
      cannot be reformed, it shall be severed from this Public License
      without affecting the enforceability of the remaining terms and
      conditions.
 .
   c. No term or condition of this Public License will be waived and no
      failure to comply consented to unless expressly agreed to by the
      Licensor.
 .
   d. Nothing in this Public License constitutes or may be interpreted
      as a limitation upon, or waiver of, any privileges and immunities
      that apply to the Licensor or You, including from the legal
      processes of any jurisdiction or authority.
 .
 .
 =======================================================================
 .
 Creative Commons is not a party to its public
 licenses. Notwithstanding, Creative Commons may elect to apply one of
 its public licenses to material it publishes and in those instances
 will be considered the "Licensor." The text of the Creative Commons
 public licenses is dedicated to the public domain under the CC0 Public
 Domain Dedication. Except for the limited purpose of indicating that
 material is shared under a Creative Commons public license or as
 otherwise permitted by the Creative Commons policies published at
 creativecommons.org/policies, Creative Commons does not authorize the
 use of the trademark "Creative Commons" or any other trademark or logo
 of Creative Commons without its prior written consent including,
 without limitation, in connection with any unauthorized modifications
 to any of its public licenses or any other arrangements,
 understandings, or agreements concerning use of licensed material. For
 the avoidance of doubt, this paragraph does not form part of the
 public licenses.
 .
 Creative Commons may be contacted at creativecommons.org."""
)


# Static preamble: upstream cloud-hypervisor and debian/* paragraphs.
HEADER = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: cloud-hypervisor
Upstream-Contact: https://github.com/cloud-hypervisor/cloud-hypervisor
Source: https://github.com/cloud-hypervisor/cloud-hypervisor

Files: *
Copyright: Cloud Hypervisor Authors
License: Apache-2.0 AND BSD-3-Clause

Files: *.md
Copyright: Cloud Hypervisor Authors
License: CC-BY-4.0

Files: scripts/* test_data/* *.toml .git* .editorconfig fuzz/Cargo.lock fuzz/.gitignore vmm/src/api/openapi/cloud-hypervisor.yaml CODEOWNERS Cargo.lock
Copyright: Cloud Hypervisor Authors
License: Apache-2.0

Files: debian/*
Copyright: 2026 Canonical Ltd.
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
        elif token == "CC-BY-4.0":
            parts.append(CC_BY_40_TEXT)
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

    # Collect the individual license identifiers that need standalone
    # License: paragraphs.  DEP-5 compound expressions like
    # "Apache-2.0 OR MIT" in a Files: paragraph reference the individual
    # License: paragraphs, so we only need one paragraph per unique
    # atomic identifier (e.g. "Apache-2.0", "MIT"), not one per compound
    # expression.  Emitting paragraphs for compound expressions triggers
    # lintian's unused-license-paragraph-in-dep5-copyright.
    all_licenses = set()
    # Seed from the static header.
    for expr in ("Apache-2.0 AND BSD-3-Clause", "Apache-2.0", "CC-BY-4.0"):
        for token in re.split(r"\s+(?:OR|AND)\s+", expr):
            token = token.strip("() ")
            if token:
                all_licenses.add(token)
    for (license_expr, _), _ in groups.items():
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

    # Emit per-file override paragraphs (last-match-wins in DEP-5)
    for override in FILE_OVERRIDES:
        dep5_license = format_dep5_license(override["license"])
        output.append(
            f"Files: {override['files']}\n"
            f"Copyright: {override['copyright']}\n"
            f"License: {dep5_license}\n"
        )
        for token in re.split(r"\s+(?:OR|AND)\s+", dep5_license):
            token = token.strip("() ")
            if token:
                all_licenses.add(token)

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
