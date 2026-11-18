# Bug report: NemoClaw v0.0.36 sandbox build fails at Step 18/62 (Patch 4)

## Summary

`nemoclaw onboard` (and `nemoclaw sandbox create`) fails building the sandbox image on
`v0.0.36`, at Dockerfile step "COPY scripts/rcf_patch.py" / the inline patch invocation
right after it. This is not environment-specific: `v0.0.36`'s Dockerfile pins its base
image by exact sha256 digest, so the failure is fully reproducible on any machine.

## Observed error

```
File "/usr/local/lib/nemoclaw/rcf_patch.py", line 81, in <module>
    assert m, "tryWriteSingleTopLevelIncludeMutation/writeConfigFile pattern not found in replaceConfigFile"
AssertionError: tryWriteSingleTopLevelIncludeMutation/writeConfigFile pattern not found in replaceConfigFile
```

## Root cause

`scripts/rcf_patch.py` (copied into the image at Dockerfile Step 17, invoked at Step 18,
what the codebase calls "Patch 4") is a regex-based patch over OpenClaw's *compiled* JS,
wrapping `replaceConfigFile` in a try/catch that suppresses `EACCES` inside the sandbox.
It is coupled to OpenClaw's internal source shape rather than to a stable public
interface. Per NemoClaw's own commit history, this patch was already rewritten three
times chasing OpenClaw refactors (issues #2377, #2484, #2876) before `v0.0.36` shipped,
and the regex stopped matching again against whatever OpenClaw version the pinned base
image (`ghcr.io/nvidia/nemoclaw/sandbox-base@sha256:3cf7c8d30f16...`) bundles
(`OpenClaw 2026.7.1` in our build).

## NVIDIA already fixed this

Commit `0ca95ce63`, `fix(dockerfile): drop Patch 4 entirely (#3500)`, merged
2026-05-15, 9 days after `v0.0.36`. It deletes the patch script, the Dockerfile step
that invokes it, and its test, and explains why the patch is unnecessary by design:

> "Patch 4 is also unnecessary by design. The EACCES it was suppressing does not happen
> in the supported flows: **Mutable-default mode** ... `openclaw.json` is `chmod 660
> sandbox:sandbox` ... no EACCES. **Shields-up mode** ... is locked to `root:root` by
> design. Shields-up exists *to refuse* runtime config and plugin mutations. Suppressing
> the EACCES masked that refusal."

Resolves NemoClaw issues #2686 and #3497. First tag without the patch: `v0.0.44`.

## What we need from NVIDIA

We were told to pin `v0.0.36` specifically for this workshop. That version cannot build
a sandbox at all as tagged. Please confirm whether:

1. `v0.0.36` was the wrong recommendation and a later tag (`v0.0.44`+) is fine to use
   instead, or
2. there is a reason to stay on `v0.0.36` that we're missing, in which case we need a
   workaround that doesn't require patching NemoClaw's own release ourselves.

## How to reproduce

```bash
git clone https://github.com/NVIDIA/NemoClaw.git && cd NemoClaw
git checkout v0.0.36
# then run onboard against this checkout, or:
git log --oneline v0.0.36..v0.0.44 -- scripts/rcf_patch.py Dockerfile
# shows 0ca95ce63, "fix(dockerfile): drop Patch 4 entirely (#3500)"
```
