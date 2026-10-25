# Shared Wire Samples

`wire-contract.json` contains published raw node facts, their reviewed projection,
and complete responses from the Kotlin producers described below.
Raw checked values and window types use the names published by `NodeFacts`.
The projector publishes `nameConsumed` and `selectionTarget` after resolving
name sources; the source reference remains internal to projection.
The same fixture carries the allowed role and action vocabulary; Python compares
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

The `captures`, `compoundResults`, `actionResults`, `waitResults`, and `fileFailures`
sections match samples by unique `name`, with exactly the same names required on
both sides. Their outer array order does not determine coverage; arrays within
each sample retain their semantic order. `entityForms` instead matches unique
`input` values and compares each collection of forms as a set.
Captures retain explicit encoding/content expectations and checks on their actual
chunks and decoded content. Python renders compound focus/fill and optional submit
steps with their known and unknown outcomes. These samples do not simulate platform
dispatch or platform sampling. Wait responses use the production `UiWait` result
owner with real `UiDelivery` published-baseline admission and supplied trees.
The scroll failure uses `UiRun.failure` after the real route and budget checks.

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

Use the [contract regeneration commands](../AGENTS.md#testing-guidelines) after an
intentional contract change. The command places the candidate under
`android/app/build`; the exporter writes only to the explicitly supplied path.
Export ends with an explicit
test failure because it has not validated the reviewed fixture. Review the generated
projection and responses before replacing the fixture, then unset
`ANDROIDCTL_CONTRACT_OUTPUT` and run both test suites.
