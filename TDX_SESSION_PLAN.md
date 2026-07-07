# TDX Support on Cloud Hypervisor -- Session Plan

## 1. Goal

Get TDX (Trust Domain Extensions) working on Cloud Hypervisor (CHV) with
the **mainline Linux kernel** TDX implementation.  All recent commits on
the `fix/tdx_snp_guard` branch are part of this effort.

## 2. Current State

### Branch: `fix/tdx_snp_guard`

The series was rebuilt from `2a3512fb7` into topic-organized commits
(oldest to newest); the original chronological bring-up history is kept
on `backup/tdx-wip-20260707` and on the fork:

```
vmm: replace tdx/sev_snp compile-time guard with runtime check
hypervisor: Update TDX support for mainline kernel ABI
arch: Skip vCPU state setup for TDX guests
hypervisor: Enable guest_memfd for TDX VMs
hypervisor: Handle memory conversion requests for TDX guests
vmm: Cap the 64-bit MMIO region advertised in the TD HOB
docs: Add TDX bring-up session notes
```

### Bugs Fixed So Far

1. **KVM_TDX_CAPABILITIES infinite loop** -- The kernel returns E2BIG
   with nent=6 when we pass nent=6; it does not tell us how many entries
   it actually needs.  The retry loop now doubles the buffer size.  The
   second attempt with 64 entries succeeds and returns 18 CPUID configs.

2. **KVM_TDX_INIT_VM EINVAL** -- Three sub-issues:
   - Unfiltered CPUID entries: the kernel requires every entry to match
     a TDX-configurable leaf.  Fixed by filtering against
     `tdx_capabilities().cpuid_configs` and AND-masking register values
     against the capability masks.
   - Invalid GPAW: CPUID `0x80000008` bits [23:16] must be 48 or 52;
     we were passing the host value (46).  Clamped to 48.
   - xfam was 0: now computed from CPUID leaf 0xd (XCR0 | XSS) masked
     against `supported_xfam`.

3. **LAPIC get_state EINVAL** -- Under TDX the guest APIC state is
   protected by the TDX module and KVM_GET_LAPIC fails.
   `arch::configure_vcpu()` unconditionally called
   `interrupts::set_lint()`.  Fixed by early-returning after
   `set_cpuid2()` when TDX is enabled, skipping MSR/register/LAPIC
   setup.

4. **guest_memfd not created for TDX** -- `guest_memfd` creation and the
   `KVM_MEM_GUEST_MEMFD` flag were gated behind `#[cfg(feature =
   "sev_snp")]`.  TDX also requires `guest_memfd` on mainline.  Widened
   the gates to `any(sev_snp, tdx)`.

### Bugs Fixed This Session (2026-07-07)

All of these were `#[cfg(feature = "sev_snp")]`-only code paths that TDX
also needs; widened to `any(sev_snp, tdx)` unless noted.  They are folded
into the topic commits listed above:

5. **KVM_TDX_INIT_MEM_REGION EINVAL (solved)** -- The old "locked vs
   unlocked ioctl path" theory was wrong: the kernel dispatches the two
   paths internally for the same ioctl number, userspace cannot pick.
   Real cause: `kvm_gmem_populate()` requires the GPA range to have
   `KVM_MEMORY_ATTRIBUTE_PRIVATE`, and the `set_memory_attributes(PRIVATE)`
   call after `KVM_SET_USER_MEMORY_REGION2` was sev_snp-gated, so TDX
   memory stayed shared.  (`hypervisor/src/kvm/mod.rs`,
   `create_user_memory_region`.)

6. **KVM_EXIT_MEMORY_FAULT unhandled** -- with memory private, implicit
   shared/private conversions killed the vCPU ("Unexpected exit
   reason").  The existing `VcpuExit::MemoryFault` handler (flips
   attributes via `KVM_SET_MEMORY_ATTRIBUTES`) was sev_snp-gated, as
   were the `vm_fd`/`memory_slots` fields on `KvmVcpu` it needs.

7. **KVM_CAP_EXIT_HYPERCALL not enabled for TDX** -- the kernel
   translates TDVMCALL<MapGPA> into KVM_HC_MAP_GPA_RANGE, but only if
   userspace enabled `KVM_CAP_EXIT_HYPERCALL`; otherwise the guest gets
   TDVMCALL_STATUS_SUBFUNC_UNSUPPORTED **silently** and TDVF limps
   along on implicit conversion with inconsistent state (virtio rings
   half-converted, guest polls a used ring it thinks is shared while
   KVM attributes say private).  Cap now enabled when memory_slots
   exist; `VcpuExit::Hypercall` arm widened to tdx.

8. **Guest maxphyaddr (46) < GPAW (48) made every MapGPA fail** -- KVM
   validates TDVMCALL GPAs against CPUID 0x80000008 EAX[7:0] via
   `kvm_vcpu_is_legal_gpa()`.  The guest sends shared GPAs with bit 47
   (= GPAW-1) set; with the default `max_phys_bits: 46` those are
   "illegal" and MapGPA fails with INVALID_OPERAND -- again silently.
   Fixed in `common_cpuid_tdx_configuration()`: leaf 0x8000_0008
   EAX[7:0] and [23:16] forced to the GPAW (48, or 52 if phys_bits >=
   52).  This must stay consistent with the GPAW clamp in `tdx_init()`.

9. **MapGPA conversion storm over the 64-bit MMIO hole** -- the TD HOB
   advertised the whole device area (up to end of guest PA space,
   ~70 TiB) as one MMIO resource; TDVF converts every MMIO HOB resource
   to shared at one MapGPA per 2 MiB = tens of millions of hypercalls
   (minutes of boot time, kernel chunks at TDX_MAP_GPA_MAX_LEN=2MiB).
   Capped the advertised region to 64 GiB (`TDX_HOB_MMIO64_MAX_SIZE` in
   `vmm/src/vm.rs`); guest OS discovers real apertures from ACPI and
   converts lazily via ioremap.

### Bugs Fixed This Session (2026-07-08)

10. **KVM_TDX_INIT_MEM_REGION EINTR (flaky, timing-dependent)** --
    Without `-v`, boot failed at the first TDVF section with
    `InitMemRegionTdx(... Interrupted system call)`; with `-v` it
    booted.  ftrace `signal_generate`/`signal_deliver` proved no POSIX
    signal ever targeted CHV.  bpftrace on `task_work_add` caught the
    real cause: the vmm thread's `block_io_uring_is_supported()` probe
    (block/src/factory.rs) creates and drops an io_uring; the kernel
    tears rings down asynchronously on an unbound kworker, which posts
    `io_tctx_exit_cb` task work with TWA_SIGNAL back to the owning
    thread.  That makes `signal_pending()` true, and
    `tdx_vcpu_init_mem_region()` checks it on every page and bails with
    -EINTR.  `-v` only added enough syscalls before boot for the task
    work to drain at a syscall boundary first.  The kernel writes its
    progress (source_addr/gpa/nr_pages) back into the region struct so
    userspace can reissue the ioctl; QEMU loops on -EINTR/-EAGAIN.
    Fixed by retrying in `tdx_init_memory_region()`
    (hypervisor/src/kvm/mod.rs), folded into the "Move
    tdx_init_memory_region ... to the Vcpu trait" commit.  12/12 boots
    without `-v` after the fix.  Not io_uring-specific: any signal
    during the long ioctl (e.g. SIGWINCH on terminal resize) trips the
    same check, so disabling the io_uring feature only hides it.

### Unit tests added (2026-07-09)

Added unit-test coverage for the testable TDX logic (per the gaps in
`TDX_REVIEW.md` section 5).  Where code was not directly testable, small
refactors were made to expose pure functions:

- `arch/src/x86_64/mod.rs`: extracted `apply_tdx_cpuid_configuration()`
  and `tdx_gpaw_from_phys_bits()` out of `common_cpuid_tdx_configuration()`
  (which needs a live `dyn Vm`), added doc comments, and added tests for
  GPAW clamping (48/52), leaf 0xd XFAM masking (XCR0 vs XSS), and the
  "unrelated leaves untouched" invariant.  Refactor + tests folded into
  the "arch, hypervisor: Fix TDX guest physical address width mismatch"
  commit.
- `arch/src/x86_64/tdx/mod.rs`: `parse_tdvf_sections()` now has tests
  driven by synthetic in-memory firmware images (valid two-section image
  plus bad signature / version / size paths), replacing reliance on a
  `tdvf.fd` binary on disk.  The old on-disk test is kept `#[ignore]`d.
  Own commit: "arch: Add unit tests for TDVF section parsing".
- `vmm/src/config.rs`: tests for `TdxFirmwareMissing`, `TdxNoCpuHotplug`,
  `TdxAndSevSnpExclusive` and a valid firmware-boot TDX config.  Own
  commit: "vmm: Add unit tests for TDX config validation".
- `arch/Cargo.toml`: `tdx = ["hypervisor/tdx"]` (was `[]`) so `arch`'s
  TDX code, which calls `vm.tdx_capabilities()`, builds standalone with
  `--features tdx`.  Folded into the "Move tdx_capabilities ... to Vm
  trait" commit.

Verified: `cargo test -p arch -p vmm --features "tdx,kvm"` green; both
rewritten commits build individually (bisect-safe).

**Known pre-existing test failure (to investigate in a later session):**
`vmm::config::unit_tests::test_config_validation` fails when built with
`sev_snp` enabled (e.g. `--features "kvm,sev_snp"` or
`--features "kvm,tdx,sev_snp"`) — the SEV-SNP `host_data` sub-block panics
on an `unwrap_err()` that returns `Ok`.  Reproduces on the pre-refactor
tree too, so it is not caused by the test work above.  CI exercises `tdx`
and `sev_snp` separately (`--features "tdx,kvm"`), so it does not block the
TDX work; the `TdxAndSevSnpExclusive` assertion only runs under the
combined feature set and is therefore currently shadowed by this earlier
failure.  Not analysed further yet.

### Debugging techniques that worked

- KVM ftrace on the host: `events/kvm/kvm_mmio`, `kvm_fast_mmio`,
  `kvm_pio` (+ filter `port != 0x608` -- TDVF Stall() polls the ACPI PM
  timer at 0x608 constantly and floods the buffer; also disable
  `kvm_exit` for the same reason).  Under TDX you cannot read vCPU
  registers, so tracing + per-thread CPU/ctxt-switch stats +
  `/proc/pid/fdinfo` eventfd counts are the main observability tools.
- `pkill -f 'cloud-hypervisor'` from an ssh command kills the ssh
  session itself (pattern matches the remote shell cmdline); use
  `pkill -f 'ubuntu/cloud-hyperviso[r]'`.
- The test script `/home/ubuntu/start-tdx-bg.sh` on the host launches
  CHV detached with serial -> /home/ubuntu/serial.log and logs ->
  /home/ubuntu/chv.log.

### Current state: FULL BOOT TO LOGIN PROMPT (2026-07-07)

With all fixes above (committed as the rebuilt series), the TDX guest
boots end to end on the mainline kernel:

```
TDVF -> grub -> Linux 7.0.0-14-generic -> systemd -> "ubuntu login:"
[    3.730545] systemd[1]: Detected confidential virtualization tdx.
```

Console gotcha: the test image's default grub entry uses
`console=hvc0`, so CHV must run with the virtio-console enabled
(`--console file=...` / `tty`), not `--console off`; serial stays
silent.  OVMF.inteltdx.ms.fd probing fw_cfg (0x510/0x511) is harmless.

`cargo +nightly fmt`, `cargo clippy` (both default and `--features
tdx`) and both builds are clean.

### Remaining work / open questions

- Boot with a NIC (`--net`), multiple disks, more vCPUs/RAM; test
  reboot and shutdown paths.
- `TDX_HOB_MMIO64_MAX_SIZE` (64 GiB) is a pragmatic cap -- revisit
  (e.g. derive from actual PCI segment apertures).
- MemoryFault (implicit conversion) path does not punch holes in
  guest_memfd on shared conversion, while the MapGPA path does --
  align?  (pre-existing asymmetry, also affects SNP).
- Quote generation / attestation (vsock + QGS) untested.
- `test_config_validation` fails under `sev_snp` (see "Known pre-existing
  test failure" above) -- investigate and fix separately.

## 3. Test Machine

```
Host:    ubuntu@10.241.201.15  (no extra ssh flags needed)
Kernel:  7.0.0-27-generic (mainline with TDX support)
QEMU:    10.2.1 (for comparison)
```

### Scripts on the test machine

- `/home/ubuntu/run-tdx-ch.sh` -- Launches a TDX guest with CHV.
  Uses `/home/ubuntu/cloud-hypervisor` as the binary.
  Firmware: `/usr/share/ovmf/OVMF.inteltdx.ms.fd`
  Disk: `/home/ubuntu/stonking.img` (qcow2)

- `/home/ubuntu/run-tdx-guest.sh` -- Launches a TDX guest with QEMU
  for comparison.  Needs `-k <kernel> -c <cmdline> -d <disk>`.

### Build and deploy workflow

```bash
# Build with TDX feature
cargo build --release --features tdx

# Copy to test machine
scp target/release/cloud-hypervisor ubuntu@10.241.201.15:/home/ubuntu/cloud-hypervisor

# Run
ssh ubuntu@10.241.201.15 "sudo /home/ubuntu/run-tdx-ch.sh 2>&1"
```

Always build with `--features tdx`.  The default build (without tdx)
should also compile cleanly -- verify both.

## 4. Codebase Architecture (TDX-relevant)

### Key files

| File | Role |
|---|---|
| `hypervisor/src/kvm/mod.rs` | KVM-level TDX ioctls: `tdx_capabilities()`, `tdx_init()`, `tdx_finalize()`, `tdx_command()`, vCPU `tdx_init()`, `tdx_init_memory_region()`.  Also `create_vm()` (VM type selection, guest_memfd setup), `create_user_memory_region()` (memory slot registration). |
| `hypervisor/src/vm.rs` | Trait declarations: `tdx_capabilities()`, `tdx_init()`, `tdx_finalize()` on `dyn Vm`. |
| `hypervisor/src/cpu.rs` | Trait declarations: vCPU-level `tdx_init()`, `tdx_init_memory_region()`. |
| `hypervisor/src/kvm/x86_64/mod.rs` | `CpuIdEntry` <-> `kvm_cpuid_entry2` conversion. |
| `arch/src/x86_64/mod.rs` | `generate_common_cpuid()`, `common_cpuid_tdx_configuration()`, `configure_vcpu()`. |
| `arch/src/x86_64/tdx/mod.rs` | TDVF section parsing, HOB table construction. |
| `vmm/src/vm.rs` | Orchestration: `init_tdx_if_enabled()`, `populate_tdx_sections()`, `init_tdx_memory()`, boot-time TDX finalization. |
| `vmm/src/cpu.rs` | `CpuManager`: `populate_cpuid()`, `configure_vcpu()`, `initialize_tdx()`, `tdx_init_memory_region()`. |
| `vmm/src/memory_manager.rs` | `add_ram_region()`, `create_userspace_mapping()` -- memory registration with KVM. |

### TDX boot sequence (current code)

```
Vm::new()
  create_vm(KVM_X86_TDX_VM)          # VM fd created as TDX type
  MemoryManager::new()                 # RAM regions allocated
    create_user_memory_region()        # KVM_SET_USER_MEMORY_REGION2
                                       #   + guest_memfd + KVM_MEM_GUEST_MEMFD
  CpuManager::populate_cpuid()
    common_cpuid_tdx_configuration()   # Mask leaf 0xd against supported_xfam
  init_tdx_if_enabled()
    tdx_init()                         # KVM_TDX_INIT_VM
                                       #   (filtered CPUID, xfam, GPAW, attrs)

Vm::boot()
  create_vcpus()                       # KVM_CREATE_VCPU
  configure_vcpu()                     # set_cpuid2 only (skip MSR/LAPIC for TDX)
  populate_tdx_sections()              # Allocate firmware RAM, load TDVF, build HOB
    add_ram_region() for BFV/CFV       #   + guest_memfd
  initialize_tdx(hob_address)
    vcpu.tdx_init(hob)                 # KVM_TDX_INIT_VCPU  <-- may silently fail?
  init_tdx_memory(&sections)
    tdx_init_memory_region(...)        # KVM_TDX_INIT_MEM_REGION  <-- EINVAL here
  tdx_finalize()                       # KVM_TDX_FINALIZE (not reached yet)
```

### `tdx_command()` -- the core ioctl wrapper

All TDX operations go through `KVM_MEMORY_ENCRYPT_OP` (ioctl `0xba`)
with a `KvmTdxCmd` struct selecting the sub-command:

```
Capabilities = 0    (VM fd)
InitVm       = 1    (VM fd)
InitVcpu     = 2    (vCPU fd, unlocked path in kernel)
InitMemRegion= 3    (vCPU fd, unlocked path in kernel)
Finalize     = 4    (VM fd)
```

The function is at `hypervisor/src/kvm/mod.rs` around line 1742.  It
logs `hw_error` from the TDX module on failure.

### Kernel TDX code reference

The mainline kernel TDX code is at `arch/x86/kvm/vmx/tdx.c`.  Key
functions (fetched and saved during analysis):

- `tdx_td_init()` -> `setup_tdparams()` -> `__tdx_td_init()`
- `tdx_vcpu_init_mem_region()` -- validates GPA alignment, private GPA
  range, then calls `kvm_gmem_populate()` per page.
- `tdx_vcpu_ioctl()` -- locked path, only handles GET_CPUID.
- `tdx_vcpu_unlocked_ioctl()` -- unlocked path, handles INIT_MEM_REGION
  and INIT_VCPU.

A full copy of `tdx.c` was saved during analysis at:
`~/.local/share/opencode/tool-output/tool_f3c4d6653001Z9YkU6yt7CEcSx`

### Important patterns

- TDX struct layouts (kvm_tdx_init_vm, kvm_tdx_capabilities, etc.) are
  built manually as raw byte buffers -- there are no generated bindings.
  This is because the mainline kernel ABI differs from earlier
  out-of-tree patches and the kvm-bindings crate has not caught up.

- Feature gating: TDX code is behind `#[cfg(feature = "tdx")]`.
  SEV-SNP is behind `#[cfg(feature = "sev_snp")]`.  Several
  infrastructure pieces (guest_memfd, memory_slots, KvmMemorySlot)
  were originally SEV-SNP-only and have been widened to
  `any(sev_snp, tdx)`.

- The `dynamic` field in CpuManager is `!tdx_enabled`.  Used as a
  proxy for "is TDX enabled" in some places.

### CONTRIBUTING.md reminders

- Commit messages: component prefix, 72-column wrap, `Signed-off-by`,
  `Assisted-by: Claude:Opus-4.6` trailer.
- Format with `cargo +nightly fmt --all`.
- Follow existing code style.
