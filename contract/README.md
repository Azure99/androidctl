# Shared Wire Samples

`wire-contract.json` contains published raw node facts, their reviewed projection,
and complete responses from the Kotlin producers described below.
Raw checked values and window types use the names published by `NodeFacts`.
The projector publishes `nameConsumed` and `selectionTarget` after resolving
name sources; the source reference remains internal to projection.
The same fixture carries the allowed role vocabulary; Python compares
it with its offline selector choices. Kotlin also checks actual node classification.
`entityForms` shares the literal and once-decoded XML entity candidates used by
device selectors and host find; candidate order is not significant.
`refAvailable` and `refReason` are published input facts here; their production
and stability across snapshots are checked separately.

The Kotlin JVM test compares the current projector and producer with the reviewed
fixture, preserving JSON types, numeric values and array order while ignoring
object key order. Python applies the same rules to projected facts, including
the distinction between booleans and numbers, and replays responses through `Result`,
`UiClient.blob`, and the XML renderer. The image payload and metadata are synthetic
inputs for raw transfer testing; they do not establish PNG or screenshot correctness.

The `captures`, `actionResults` and `fileFailures` sections match samples by
unique `name`, with exactly the same names required on both sides. Their outer
array order does not determine coverage; arrays within each sample retain their
semantic order. `entityForms` instead matches unique `input` values and compares
each collection of forms as a set. Captures retain explicit encoding/content
expectations and checks on their actual chunks and decoded content.
`actionResults` contains plain-click, selector-click and coordinate-pending-at-expiry
from the production
`UiRun` result owner with supplied receipt facts. These samples do not simulate
platform dispatch or platform sampling.

The fixture also records `OutputStore` metadata and bounded chunks for both shell
streams, consumed by Python's `JobClient.output`, plus `fileFailures` results
rendered with their original job identity, error and exit status. `shellCleanupFailure`
comes from `ShellProtocol`; Python verifies its explicit public exit while preserving
the execution and cleanup facts. Actual file I/O,
publication and helper cleanup are checked separately on a device.

`limits` records the shared wire limits, the pairing name limit in UTF-16 units,
and the helper frame payload size. `queryMethods` records the Gateway's actual
query pool, including `pair.register` in addition to the host pool. Kotlin checks
each section against the reviewed fixture in the normal test run; Python compares
the fixture with its own limits and pool. Native output and frame limits retain
two direct source checks because C does not participate in the JVM exporter.

Validate both sides from the repository root:

```powershell
& .\android\gradlew.bat -p .\android :app:testDebugUnitTest --tests 'com.rainng.androidctl.WireContractTest' --rerun-tasks
conda run -p ./.conda python -m pytest androidctl/tests/test_wire_contract.py
```

For an intentional contract change, export to a separate candidate:

```powershell
$env:ANDROIDCTL_CONTRACT_OUTPUT = Join-Path (Get-Location) 'android/app/build/wire-contract.candidate.json'
try {
    & .\android\gradlew.bat -p .\android :app:testDebugUnitTest --tests 'com.rainng.androidctl.WireContractTest.deviceProjectionAndTransferMatchTheSharedContract' --rerun-tasks
} finally {
    Remove-Item Env:ANDROIDCTL_CONTRACT_OUTPUT
}
```

Export intentionally fails after writing the candidate and does not validate the
reviewed fixture. Read that failure, review the differences, then replace the
fixture only when the producer and consumer change is intended. With the output
variable unset, run both validations again.
