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

For the Android APK only, run `task release:android`. For the complete
Android and Python candidate, run `task release:pypi`. Neither publishes
anything. Preserve any previous candidate that must remain traceable before
rebuilding the same version.

| Task | Work performed, in order |
| --- | --- |
| `release:android` | Gradle build, require a clean source tree, stage the public APK, write SHA256SUMS and verify. |
| `release:pypi` | Gradle build, require a clean source tree, stage/checksum/verify the APK, build sdist then wheel, run Twine and smoke one clean wheel install. |

`task android:build:release` only runs Gradle `assembleRelease`.
`task python:quality` checks all Python source and behavior, including release tools.

Use module commands when consuming existing artifacts without implicit build
predecessors:

```sh
python -m tools.release.android_release verify
python -m tools.release.pypi_release check
python -m tools.release.pypi_release install
```

The Android module's `prepare` requires a clean source tree before staging
`android/app/build/outputs/apk/release/app-release.apk`, writing its checksum and
verifying it. The PyPI module has four entries:

| Command | Work performed, in order |
| --- | --- |
| `build` | Require a clean source tree, verify the staged APK and checksum, then build and inspect sdist/wheel. |
| `check` | Verify the staged APK and existing sdist/wheel, then run Twine. |
| `install` | Verify the staged APK and existing sdist/wheel, then smoke one clean wheel install. |
| `prepare` | Require a clean source tree, stage and checksum the current Gradle APK, then perform build, check and install on the same candidate. |

Neither module invokes Gradle. Use module `--help` for arguments.
`--repo-root PATH` before the subcommand selects a different input tree; module
imports still require the repository root on Python's module search path.

Independent APK verification needs only the candidate APK, `SHA256SUMS` and
`VERSION`, plus SDK `aapt2` and `apksigner`. It checks package ID, version name,
SHA-256 and signature, and reports the actual `versionCode`. Gradle alone derives
the code. Verification does not prove equality with the current Gradle output,
that the code follows the formula, or that it permits an upgrade. The build
directory can be removed without preventing verification of a retained candidate.

Every independent PyPI entry verifies current APK manifest, signature and bytes,
and checks wheel/sdist metadata and their sole APK resource. Device testing and
approval of the signing identity remain separate release inputs.

Android assets and verification logs are under `dist/release/android/<version>`.
Python artifacts are under `dist/release/pypi/<version>/build-output`; build,
Twine and installation logs are in its parent directory. A failed Android CI job
retains any generated text logs and Android reports in the
`android-failure-evidence` artifact. Both `prepare` commands require a clean Git
source tree before staging; independent PyPI `build` also checks before building.
A source copy without `.git` skips these checks.
Building replaces the same-version build-output and related
logs. Building uses an isolated source copy with the verified APK, creates the
sdist and then builds the wheel from it. Original package resources, build and
egg-info directories remain untouched.

The smoke check installs the wheel in one isolated venv, runs console and module
help away from the source root, and checks the version and exported APK/skill
against the source candidate's hashes and file bytes. Success removes the venv;
failure retains it and its log for diagnosis. The tool does not pass
`constraints-ci.txt` to pip; record the resolved dependencies with the result.
This smoke check covers these basic entries and resources on the current platform
and Python, rather than daemon, TLS or device behavior. Retain the source commit
and artifact hashes with validation results so the candidate can be identified
beyond its version string.

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
   through the approved tag. Upload the verified wheel and sdist with
   `python -m twine upload` followed by
   their two exact paths from `build-output`, using the intended PyPI account.
   Confirm the published version and downloadable assets. PyPI does not allow
   [reusing an uploaded filename](https://docs.pypi.org/publishing/troubleshooting/#why-am-i-getting-a-filename-or-contents-already-exists-or-filename-has-been-previously-used-error), including
   after deletion.
5. After publishing, restore repository-relative README links on `main` in a new
   commit. Keep the release tag and generated release assets unchanged so
   published packages retain their versioned links.
