# Release Operations

Run commands from the repository root with the development Python environment
described in [Repository Guidelines](../../AGENTS.md#build-test-and-development-commands).

## Prerequisites

In addition to the development environment, the Python release tools need
`aapt2` and `apksigner` on PATH or
under `ANDROID_HOME` / `ANDROID_SDK_ROOT`; `local.properties` alone is insufficient
for those tools.

`VERSION` contains ASCII `MAJOR.MINOR.PATCH`; surrounding whitespace is trimmed.
It supplies the Python package version and Android `versionName`.
Android `versionCode` is `major * 1_000_000 + minor * 1_000 + patch`, with
major in 0-2100, minor/patch in 0-999 and the total in 1-2_100_000_000.

Release signing reads these environment variables:

- `ANDROIDCTL_RELEASE_STORE_FILE`: path to an existing private keystore.
- `ANDROIDCTL_RELEASE_STORE_PASSWORD`: its store password.
- `ANDROIDCTL_RELEASE_KEY_ALIAS`: the signing key alias.
- `ANDROIDCTL_RELEASE_KEY_PASSWORD`: its key password.

If the store and key share a password, supply it for both variables. Missing
inputs or a missing keystore fail release packaging; AGP checks the password
and alias. Debug, unit tests and lint do not require release signing inputs.
Keep keys and credentials outside source control. Preserve the existing signing
identity for upgrades; these tools do not implement key rotation. A new identity
can be created with interactive `keytool -genkeypair -keystore PATH -alias
androidctl-release -storetype JKS -keyalg RSA -keysize 4096 -validity 3650`.

Release builds enable R8 code and resource shrinking. Changes to reflection,
service discovery or serialization need verification in the release APK.

## Local Candidates

Run `task release:android` to prepare the Android APK candidate. It does not
publish anything. Preserve any previous candidate that must remain traceable
before rebuilding the same version.

| Task | Work performed, in order |
| --- | --- |
| `release:android` | Gradle build, require a clean source tree, stage the public APK, write SHA256SUMS and verify. |

`task android:build:release` only runs Gradle `assembleRelease`.
`task python:quality` checks all Python source and behavior, including release tools.

Use the module command when consuming an existing candidate without implicit
build predecessors:

```sh
python -m tools.release.android_release verify
```

The Android module's `prepare` requires a clean source tree before staging
`android/app/build/outputs/apk/release/app-release.apk`, writing its checksum and
verifying it.

The module does not invoke Gradle. Use module `--help` for arguments.
`--repo-root PATH` before the subcommand selects a different input tree; module
imports still require the repository root on Python's module search path.

Independent APK verification needs only the candidate APK, `SHA256SUMS` and
`VERSION`, plus SDK `aapt2` and `apksigner`. It checks package ID, version name,
SHA-256 and signature, and reports the actual `versionCode`. Gradle alone derives
the code. Verification does not prove equality with the current Gradle output,
that the code follows the formula, or that it permits an upgrade. The build
directory can be removed without preventing verification of a retained candidate.

Device testing and approval of the signing identity remain separate release inputs.

Android assets and verification logs are under `dist/release/android/<version>`.
The `prepare` command requires a clean Git source tree before staging.
A source copy without `.git` skips this check.

## Publishing An Approved Candidate

Publishing is a separate manual operation. The release tools do not create tags,
push commits, upload packages or create a GitHub Release.

1. Set `VERSION` and prepare release notes describing the actual capabilities.
   Change README repository-relative links to absolute GitHub URLs for the
   intended `vX.Y.Z` tree, including their anchors, so the packaged README leads
   readers to that version. Commit these release inputs before building.
   Run quality checks and candidate preparation
   on that clean commit using the intended signing identity. Verify the resulting
   candidate and, when replacing an installed app, its upgrade path.
2. Once that candidate is approved, create an annotated `vX.Y.Z` tag on its
   source commit (`git tag -a vX.Y.Z -m "AndroidCtl X.Y.Z"`). Push the approved
   commit and tag to the intended remote. Do not move an existing release tag
   to a different candidate.
3. Create a draft GitHub Release from that tag using `gh release create vX.Y.Z
   --verify-tag --draft`, supplying the finalized release notes. Run
   `gh release upload vX.Y.Z dist/release/android/X.Y.Z/androidctl-agent-X.Y.Z-release.apk
   dist/release/android/X.Y.Z/SHA256SUMS --clobber` after checking those paths.
   `--clobber` replaces same-named assets in the draft. Check the attached
   assets, then publish with `gh release edit vX.Y.Z --draft=false`. Upload before
   publishing also supports repositories with immutable releases, as described
   in the [GitHub CLI guide](https://cli.github.com/manual/gh_release_create).
4. Check that the README's versioned pages and anchors are publicly reachable
   through the approved tag.
5. After publishing, restore repository-relative README links on `main` in a new
   commit. Keep the release tag and generated release assets unchanged so
   published packages retain their versioned links.
