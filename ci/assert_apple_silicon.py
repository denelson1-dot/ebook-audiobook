"""Assert that an Apple Silicon Mac really got a working Metal build.

The rest of CI installs with ``--no-tts``, which proves the app installs but
says nothing about the engine — and the engine is the part that differs most by
platform. On Apple Silicon, three separate things have to line up, and each has
failed at some point in some project's history:

* pip has to resolve a torch wheel that exists for macOS arm64 at all;
* that wheel has to have been *built* with the Metal backend compiled in;
* Metal has to be available at runtime, which needs macOS 12.3 or newer.

A CPU fallback would still convert books, just several times slower, and it
would do so silently. This turns that silence into a failed build.

Every Apple Silicon generation — M1 through M5 — is ``arm64`` and takes exactly
this path; there is no per-chip branch anywhere in the installer, which is why
one runner is enough to cover the family.
"""

from __future__ import annotations

import os
import platform
import sys


def main() -> int:
    failures: list[str] = []

    system, machine = platform.system(), platform.machine()
    print(f"platform: {system} {platform.release()} ({machine})")
    if system != "Darwin":
        print("SKIP: not macOS — this check only means something on a Mac.")
        return 0
    if machine not in ("arm64", "aarch64"):
        print(f"SKIP: {machine} is not Apple Silicon (an Intel Mac is CPU-only "
              "by design — PyTorch stopped building for them after 2.2.2).")
        return 0

    # Set by ebook_audiobook/__init__ before torch is imported. Without it, any
    # op Metal lacks raises instead of falling back to the CPU, which surfaces
    # as a crash partway through a render rather than a slower one.
    import ebook_audiobook  # noqa: F401  (imported for that side effect)

    fallback = os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK")
    print(f"PYTORCH_ENABLE_MPS_FALLBACK={fallback!r}")
    if fallback != "1":
        failures.append("PYTORCH_ENABLE_MPS_FALLBACK was not set before torch loaded")

    try:
        import torch
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: torch did not import: {e}")
        return 1
    print(f"torch: {torch.__version__}")

    if not torch.backends.mps.is_built():
        failures.append("this torch wheel was built without the Metal backend")
    if not torch.backends.mps.is_available():
        failures.append(
            "Metal is not available at runtime (macOS older than 12.3, or a "
            "CPU-only wheel was installed)")

    from ebook_audiobook import device

    dev = device.select_device()
    print(f"selected device: {dev.kind} — {dev.describe()}")
    if dev.kind != "mps":
        failures.append(
            f"the app chose {dev.kind!r} on Apple Silicon; it should choose 'mps'. "
            "Rendering would silently run several times slower.")

    # Prove it end to end rather than trusting the capability flags.
    try:
        x = torch.ones(8, device="mps") * 2
        assert float(x.sum()) == 16.0
        print("a tensor computed on the GPU: ok")
    except Exception as e:  # noqa: BLE001
        failures.append(f"computing on the Metal device failed: {e}")

    print()
    if failures:
        print("FAIL: Apple Silicon acceleration is not working:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Apple Silicon acceleration is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
