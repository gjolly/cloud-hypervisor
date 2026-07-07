# TDX Mainline Bring-Up Review

**Branch range**: `d84859df8..HEAD^` (excludes `cea04303b` "docs: Add TDX bring-up session notes")

**Files changed**: 8 files, +460 / -203

---

## Summary

This patch series adapts Cloud Hypervisor's TDX support from the out-of-tree KVM
patchset ABI to the mainline kernel's TDX ABI (v6.14+). It is generally
well-structured, correct, and safe. No critical or high-severity issues were
found after cross-referencing against the kernel source at `~/tmp/linux`.

The three notable issues requiring action are:
1. A `ptr::read` alignment concern (safe in practice on all supported platforms, but should use `ptr::read_unaligned`)
2. A `common_cpuid_tdx_configuration()` now being `pub` without a doc comment
3. A fragile bitwise check for measurement attributes (`== 1` vs `& 1 != 0`)

---

## 1. Rust Coding Style and Good Practices

### Alignment UB via `ptr::read` on `Vec<u8>` [MEDIUM] — ✅ DONE (2026-07-09)

`hypervisor/src/kvm/mod.rs`, `tdx_capabilities()` (~line 1542):

```rust
let entry: kvm_bindings::kvm_cpuid_entry2 =
    unsafe { ptr::read(buf.as_ptr().add(entry_offset).cast()) };
```

`Vec<u8>` only guarantees 1-byte alignment; `kvm_cpuid_entry2` requires 4-byte
alignment (it contains `u32` fields). `ptr::read` on a misaligned pointer is UB
per the Rust reference. In practice the global allocator returns at least
pointer-aligned memory, and `HEADER_SIZE` (2056) is 8-byte aligned, so this is
safe on all x86_64 targets. **Recommend**: use `ptr::read_unaligned` for
correctness-by-construction.

### Missing doc comment on newly-public function [MEDIUM] — ✅ DONE (2026-07-09)

`arch/src/x86_64/mod.rs:902`, `common_cpuid_tdx_configuration()` — now `pub` and
re-exported from `arch/src/lib.rs:125`. No `///` doc comment. The function
performs non-trivial GPAW fixup and XFAM masking. A doc comment explaining the
purpose and the GPAW/0xd leaf logic is warranted.

**Resolved**: doc comments added. The GPAW/XFAM logic was extracted into a
documented `apply_tdx_cpuid_configuration()` helper plus a
`tdx_gpaw_from_phys_bits()` helper, both carrying `///` comments.

### Unused `vm_ref` clone when TDX is disabled [LOW]

`vmm/src/vm.rs:791` — `let vm_ref = vm.clone()` clones an `Arc` unconditionally
but is only used under `#[cfg(feature = "tdx")]`. Cheap (one atomic refcount
bump), but could be moved inside the `cfg` block.

### `#[allow(unused_mut)]` pattern [LOW]

`vmm/src/cpu.rs:960` — correct pattern for suppressing the "variable does not
need to be mutable" warning when `feature = "tdx"` is off. Consistent with codebase.

### SAFETY comments [OK]

All `unsafe` blocks have `SAFETY:` comments documenting the invariants. Good.

---

## 2. Design Review

### Trait method moves: CORRECT

| Method | Before | After | Rationale |
|--------|--------|-------|-----------|
| `tdx_capabilities()` | `Hypervisor` trait (system fd) | `Vm` trait (VM fd) | Mainline ioctl operates on VM fd |
| `tdx_init_memory_region()` | `Vm` trait (VM fd) | `Vcpu` trait (vCPU fd) | Mainline ioctl operates on vCPU fd |

These moves match the mainline kernel semantics exactly.

### SEV-SNP/TDX infrastructure sharing: CORRECT

Broadening `#[cfg(feature = "sev_snp")]` to `#[cfg(any(feature = "sev_snp", feature = "tdx"))]` for
guest_memfd, memory_attributes, and hypercall paths is architecturally sound.
Both CoCo technologies share the same KVM private memory infrastructure.

### Duplicate GPAW logic [MEDIUM] — 🟢 RESOLVED (WON'T DEDUP) (2026-07-09)

`arch/src/x86_64/mod.rs:930-934` and `hypervisor/src/kvm/mod.rs:1629-1635` both
clamp the guest physical address width in CPUID leaf 0x80000008, with slightly
different behavior (arch layer writes both EAX[7:0] and EAX[23:16]; hypervisor
layer writes only EAX[23:16]). The split responsibility is intentional but
subtle. **Recommend**: extract a shared helper `fn tdx_gpaw_from_phys_bits(phys_bits: u32) -> u32`.

**Resolved (won't dedup)**: `tdx_gpaw_from_phys_bits()` was extracted in the
`arch` layer and is now used by `common_cpuid_tdx_configuration()`. The
`hypervisor` layer's `tdx_init()` keeps its own inline clamp *by design*, not as
leftover duplication:

- A truly *shared* helper would have to live in `hypervisor` (arch → hypervisor,
  so it can't go in `arch`). But GPAW-from-phys_bits is x86 CPUID/EPT semantics,
  which belongs in the `arch` layer; pushing it into the backend-abstraction
  crate to satisfy DRY inverts the layering for a one-line expression.
- The two clamps are not one duplicated computation — they are two layers each
  enforcing an invariant they own. `arch` applies *policy* while building the
  guest CPUID; `hypervisor::tdx_init()` is a public `Vm`-trait method that
  *defensively normalizes* the CPUID it is handed at the API boundary, since it
  cannot assume the caller pre-clamped it.
- Collapsing them into one shared symbol would invite removal of the "redundant"
  hypervisor clamp and silently break the hypervisor crate's standalone
  contract.

The real risk was mistaking the two clamps for accidental copy-paste, so the
`hypervisor` clamp now carries a comment documenting that it is a deliberate
defensive normalization and that the canonical policy lives in
`arch::tdx_gpaw_from_phys_bits`.

### `CpuManager::populate_cpuid` takes an explicit `vm` parameter [LOW]

`vmm/src/cpu.rs:954` — `CpuManager` already stores `self.vm: Arc<dyn hypervisor::Vm>`.
The explicit `vm: &dyn hypervisor::Vm` parameter is redundant; `self.vm.as_ref()` could
be used instead, eliminating the `vm_ref` clone at the call site.

### `hypervisor_vm()` accessor is dead code [LOW] — ✅ DONE (2026-07-09)

`vmm/src/vm.rs:2993` — `pub fn hypervisor_vm(&self) -> &dyn hypervisor::Vm` has
no callers. Adding it exposes an internal trait object through the VMM's public
API. Either include its caller in the same commit or remove it.

**Resolved**: the unused accessor was removed, folded into the commit that
introduced it (`hypervisor: Move tdx_capabilities from Hypervisor to Vm trait`).

### `#[allow(dead_code)]` on KvmMemorySlot [LOW]

`hypervisor/src/kvm/mod.rs:617` — moved from per-field attributes to struct-level.
The struct-level attribute loses the ability to detect if individual fields become
dead under specific feature configurations. Prefer per-field attributes.

### Layer boundaries: CORRECT

The change respects the three-layer architecture (arch → hypervisor → vmm).
The arch layer's `use hypervisor::HypervisorVmError` is consistent with the
existing `use hypervisor::HypervisorError`.

---

## 3. TDX Security and Correctness Review

All findings verified against the kernel source at `~/tmp/linux/arch/x86/kvm/vmx/tdx.c`.

### XFAM handling: CORRECT

**This was flagged as a potential high-severity issue by the initial static
review — the old code used two fields (`xfam_fixed0` AND + `xfam_fixed1` OR)
while the new code uses only `supported_xfam` (AND).**

Kernel source verification shows this is **correct**:

1. `tdx_get_supported_xfam()` in the kernel (`tdx.c:101-111`) computes
   `supported_xfam` as `(host_xcr0 | host_xss) & td_conf->xfam_fixed0` —
   i.e., it already incorporates the AND mask.

2. During `KVM_TDX_INIT_VM`, the kernel ORs `xfam_fixed1` into the
   userspace-provided xfam (`tdx.c:2414`): `td_params->xfam = init_vm->xfam | td_conf->xfam_fixed1`.

3. The kernel validates that `init_vm->xfam` contains no unsupported bits
   (`tdx.c:2409`): `if (init_vm->xfam & ~tdx_get_supported_xfam(td_conf))`.

Therefore:
- The Rust code's `xfam &= caps.supported_xfam` is correct — it restricts to
  the kernel-supported set.
- The kernel adds the `xfam_fixed1` bits internally.
- The guest-visible CPUID in `common_cpuid_tdx_configuration()` AND-masking
  with `supported_xfam` is correct for the same reason.

### GPAW handling: CORRECT

The kernel's `setup_tdparams_eptp_controls()` (`tdx.c:2325`) requires
`guest_pa` from CPUID[0x80000008].EAX[23:16] to be exactly 48 or 52. The
Rust code's `if phys_bits >= 52 { 52 } else { 48 }` is correct.

### Memory measurement (MRTD): CORRECT with minor fragility [LOW] — ✅ DONE (2026-07-09)

`vmm/src/vm.rs:2717`: `section.attributes == 1` checks for
`TDVF_SECTION_ATTRIBUTES_EXTENDMR`. Correct for current TDVF binaries but
fragile — if future TDVF sets reserved bits alongside bit 0, this would
incorrectly skip measurement. **Recommend**: `section.attributes & 1 != 0`.

**Resolved**: changed to `section.attributes & 1 != 0`, folded into the commit
that rewrites this call site (`hypervisor: Move tdx_init_memory_region from the
Vm trait to the Vcpu trait`).

### Shared/private memory conversion: CORRECT

The `KVM_HC_MAP_GPA_RANGE` handler (shared between SEV-SNP and TDX) correctly:
1. Sets `KVM_MEMORY_ATTRIBUTE_PRIVATE` or clears it via `set_memory_attributes`
2. Punches holes in guest_memfd for shared→private transitions

The limitation to 4K pages (TODO for 2MB) is acceptable for initial bring-up.

### HOB MMIO cap (64 GiB): SAFE

The cap at `TDX_HOB_MMIO64_MAX_SIZE = 64 << 30` is safe because:
- The HOB MMIO is consumed only by TDVF at boot for BAR allocation
- The guest OS discovers full PCI apertures from ACPI
- Capping avoids tens of millions of unnecessary MapGPA TDVMCALLs

### Memory attributes initialization: CORRECT

Setting `KVM_MEMORY_ATTRIBUTE_PRIVATE` on all memory slots when
`memory_slots.is_some()` is correct. `KVM_TDX_INIT_MEM_REGION` requires this,
otherwise `kvm_gmem_populate()` returns EINVAL.

### vCPU state skip: CORRECT

Skipping MSR, register, and LAPIC setup for TDX vCPUs is correct — the TDX
module initializes these during TDH.VP.INIT. The `enable_hyperv_synic()` call
before the early return is a non-issue because `kvm_hyperv` is false for TDX.
Moving the early return before that call would be cleaner but is not a bug.

### TdxCapabilities buffer layout: CORRECT (comment typo only)

The `HEADER_SIZE` in both `tdx_capabilities` and `tdx_init` is verified correct against
the kernel struct definitions:

- **KVM_TDX_CAPABILITIES**: HEADER_SIZE = 2056 (6×8 + 250×8 + 4+4). The code
  comment says "2080" which is a comment-only arithmetic error.

- **KVM_TDX_INIT_VM**: HEADER_SIZE = 264 (2×8 + 3×6×8 + 12×8 + 4+4).
  Matches the kernel's 256-byte `TD_PARAMS` + 8-byte `kvm_cpuid2` header.

---

## 4. Kernel Interface Review

All kernel structures verified against `/tmp/linux/arch/x86/include/uapi/asm/kvm.h`:

| Structure | Rust Code | Kernel Source | Match? |
|-----------|-----------|---------------|--------|
| `kvm_tdx_capabilities` | HEADER_SIZE=2056, nent_offset=2048 | 6×u64 + reserved[250] + kvm_cpuid2 | Correct |
| `kvm_tdx_init_vm` | HEADER_SIZE=264, fields match | attributes, xfam, mrconfigid/owner/ownerconfig[6], reserved[12] | Correct |
| `kvm_tdx_init_mem_region` | `source_addr`, `gpa`, `nr_pages` | Same field names | Correct |
| `kvm_cpuid2` | `{ nent: u32, padding: u32 }` = 8 bytes | Same layout | Correct |

### Interface-specific findings: ALL CORRECT

- **`KVM_CAP_EXIT_HYPERCALL`**: Correctly enabled with `check_extension_int` mask when `memory_slots.is_some()`. Cast `i32 → u64` is safe.
- **`KVM_MEM_GUEST_MEMFD`**: Correctly OR'd into flags when guest_memfd is active.
- **guest_memfd**: `kvm_create_guest_memfd` + `OwnedFd` + per-slot tracking. Correct creation and cleanup.
- **`KVM_MEMORY_ATTRIBUTE_PRIVATE`**: Set at slot creation, correct for TDX boot state.
- **`KVM_HC_MAP_GPA_RANGE`**: GPA/npages/attributes handling correct; TODO for 2MB pages acknowledged.

---

## 5. Gaps: Missing Tests, Documentation, and Defensive Checks

### Missing Unit Tests — 🟡 PARTIAL (2026-07-09)

Unit tests added for items 3, 4 and 5 below (see `TDX_SESSION_PLAN.md`,
"Unit tests added (2026-07-09)"). Items 1 and 2 remain open.

1. ⬜ **`tdx_capabilities()` buffer construction** (`mod.rs:1479-1583`): HEADER_SIZE,
   nent_offset, E2BIG retry loop, 4096-entry guard. The buffer layout is
   fragile and should be validated programmatically. *(Not done — the buffer
   is built inside the ioctl-driven method; testing it needs the layout
   constants extracted first.)*

2. ⬜ **`tdx_init()` CPUID filtering** (`mod.rs:1589-1711`): Filtering by
   (function, index), bitmask AND operation, GPAW clamping, xfam computation.
   *(Not done — logic is entangled with the live ioctl call; would need the
   filtering extracted into a pure helper.)*

3. ✅ **`common_cpuid_tdx_configuration()`** (`arch/src/x86_64/mod.rs:902-937`):
   XFAM mask application for leaf 0xd indices 0 and 1, GPAW fixup.
   *(Done — extracted `apply_tdx_cpuid_configuration()` and added tests for
   XFAM masking, GPAW clamping, and the "unrelated leaves untouched" invariant.)*

4. ✅ **Config validation** (`vmm/src/config.rs`): The `TdxFirmwareMissing`,
   `TdxNoCpuHotplug`, and `TdxAndSevSnpExclusive` validation paths have no test coverage.
   *(Done — added to `test_config_validation`. Note: the `TdxAndSevSnpExclusive`
   assertion only runs under `--features "tdx,sev_snp"`, where a pre-existing,
   unrelated `test_config_validation` failure currently shadows it — see
   `TDX_SESSION_PLAN.md`.)*

5. ✅ **`test_parse_tdvf_sections`** (`arch/src/x86_64/tdx/mod.rs:523`): Already
   `#[ignore]`'d due to requiring a TDVF binary on disk. Consider creating a
   synthetic in-memory blob for CI.
   *(Done — added synthetic in-memory image tests for the valid parse plus the
   bad signature / version / size paths. The on-disk test stays `#[ignore]`'d.)*

### Missing Integration Tests

`cloud-hypervisor/tests/integration_cvm.rs` has **zero TDX tests** — all
existing CoCo tests are gated behind `#[cfg(feature = "sev_snp")]`. Scenarios
that need coverage:

- Basic TDX VM launch with TDVF firmware
- TDX with multiple vCPUs
- TDX with virtio devices (block, net, console)
- TDX config validation (missing firmware, boot!=max, SEV-SNP mutual exclusion)
- TDX coredump prevention

### Missing Documentation Updates — ✅ DONE (2026-07-09)

`docs/intel_tdx.md` needs updates for:

1. Mainline kernel ioctl usage (`KVM_TDX_CAPABILITIES`, `KVM_TDX_INIT_VM`,
   `KVM_TDX_INIT_MEM_REGION`) vs the old out-of-tree ABI
2. HOB MMIO64 cap (64 GiB) and its implications
3. GPAW clamping behavior (48/52 forced)
4. `boot != max` vCPU restriction clarification
5. `tdx_disable_filter` guest kernel parameter and its interaction with CPUID filtering

**Resolved**: all five items documented in `docs/intel_tdx.md` under new
"Mainline kernel ABI" and "TDX-specific behavior and limitations" sections, in
its own commit (`docs: Document mainline TDX kernel ABI and behavior`).

### Additional Defensive Gaps

| Issue | Location | Severity |
|-------|----------|----------|
| `hob_address == 0` not validated before tdx_init ioctl | `vmm/src/cpu.rs:1698` | LOW |
| `UnknownTdxVmCall` error lacks subfunction/type detail | `hypervisor/src/cpu.rs:307` | LOW |
| `section.size.try_into().unwrap()` panics if too large | `vmm/src/vm.rs:2706` | LOW (guarded by 32-bit compile error) |
| `nr_pages` conversion could panic on non-page-aligned size | `mod.rs:3615` | LOW |
| No logging in `tdx_init_memory_region()`, `tdx_finalize()`, or `initialize_tdx()` per-vCPU loop | multiple locations | LOW |
| GPAW clamping is silent — significant guest-visible change not logged | `arch/mod.rs` and `kvm/mod.rs` | LOW |

---

## 6. Overall Assessment

**The patch is correct, safe, and well-structured.** It successfully ports the
TDX support from the out-of-tree KVM ABI to the mainline kernel ABI. The
architectural decisions (moving `tdx_capabilities` to `Vm`, `tdx_init_memory_region` to
`Vcpu`, sharing guest_memfd infrastructure with SEV-SNP) are sound.

**Recommended actions before merge:**

1. ✅ **[Medium]** Replace `ptr::read` with `ptr::read_unaligned` in `tdx_capabilities()` *(done 2026-07-09)*
2. ✅ **[Medium]** Add `///` doc comment to `pub fn common_cpuid_tdx_configuration()` *(done 2026-07-09)*
3. ✅ **[Low]** Change `section.attributes == 1` to `section.attributes & 1 != 0` *(done 2026-07-09; folded into the `tdx_init_memory_region` move commit)*
4. ✅ **[Low]** Extract shared GPAW helper to reduce duplication risk *(resolved won't-dedup: extracted `tdx_gpaw_from_phys_bits()` in the `arch` layer; `hypervisor` layer's clamp is a deliberate defensive normalization at the `Vm`-trait boundary and now documents that the canonical policy lives in `arch`)*
5. ✅ **[Low]** Remove or document the dead `hypervisor_vm()` accessor *(done 2026-07-09; removed, folded into the `tdx_capabilities` move commit)*
6. ✅ **[Low]** Update `docs/intel_tdx.md` with the new mainline kernel details *(done 2026-07-09; own commit)*

_Also added this session (not in the original list): unit tests for TDVF
section parsing and TDX config validation, and `arch/Cargo.toml` now sets
`tdx = ["hypervisor/tdx"]` so the `arch` crate builds standalone with
`--features tdx`._
