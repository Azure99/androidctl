# Shared Wire Contract

`wire-contract.json` records the current message/JSON/pairing-name limits and the
Gateway query-method set, plus retained shell output and cleanup facts. Kotlin produces these facts; Python verifies its limits
and its query set (the Gateway additionally accepts `pair.register`).

The fixture records `OutputStore` metadata and bounded chunks for both shell
streams, consumed by Python `JobClient.output`. `shellCleanupFailure` comes from
`ShellProtocol`; Python verifies its explicit public exit while preserving execution
and cleanup facts. Native output/frame constants are checked directly because C
does not participate in the JVM producer. Actual helper execution and cleanup
require the separate device checks.

`fileFailures` records device publication uncertainty and definite rejection.
Kotlin compares named samples independent of their outer order, with exact name
coverage; Python consumes each original job, Problem and public exit.

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
