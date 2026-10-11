package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.util.Base64
import java.util.Random
import java.util.zip.InflaterInputStream

class WireContractTest {
    @Test fun deviceProjectionAndTransferMatchTheSharedContract() {
        val fixture = javaClass.getResourceAsStream("/wire-contract.json")!!.bufferedReader().use { JSONObject(it.readText()) }
        val produced = generateContract(fixture)
        System.getenv("ANDROIDCTL_CONTRACT_OUTPUT")?.let { exportCandidate(produced, it) }
        compareContract(fixture, produced)
    }

    @Test fun contractComparisonRejectsAnExtraTopLevelSection() {
        val fixture = javaClass.getResourceAsStream("/wire-contract.json")!!.bufferedReader().use { JSONObject(it.readText()) }
        val produced = generateContract(fixture).put("extra", JSONObject())
        val failure = assertThrows(AssertionError::class.java) { compareContract(fixture, produced) }
        assertTrue(failure.message.orEmpty().contains("Contract sections differ"))
    }

    @Test fun namedSamplesIgnoreOnlyTheirOuterOrder() {
        val first = json("name" to "first", "nodes" to JSONArray(listOf(1, 2)))
        val second = json("name" to "second", "nodes" to JSONArray(listOf(3)))
        val reviewed = JSONArray(listOf(first, second))
        compareNamedSamples("actionResults", reviewed, JSONArray(listOf(second, first)))

        for (changed in listOf(
            JSONArray(listOf(first)),
            JSONArray(listOf(first, second, json("name" to "extra"))),
            JSONArray(listOf(copy(first).put("nodes", JSONArray(listOf(2, 1))), second)),
        )) {
            val failure = assertThrows(AssertionError::class.java) { compareNamedSamples("actionResults", reviewed, changed) }
            assertTrue(failure.message.orEmpty().contains("actionResults"))
        }
        val duplicates = JSONArray(listOf(first, first, second))
        for ((saved, actual) in listOf(reviewed to duplicates, duplicates to reviewed)) {
            val failure = assertThrows(AssertionError::class.java) { compareNamedSamples("actionResults", saved, actual) }
            assertTrue(failure.message.orEmpty().contains("duplicate name: first"))
        }
        val forms = JSONArray(listOf(json("input" to "&amp;"), json("input" to "&amp;")))
        val failure = assertThrows(AssertionError::class.java) { indexSamples("entityForms", forms, "input") }
        assertTrue(failure.message.orEmpty().contains("duplicate input: &amp;"))
    }

    private fun generateContract(fixture: JSONObject): JSONObject {
        val projected = copy(fixture.getJSONObject("raw"))
        UiSemantics.project(projected.getJSONArray("nodes").objects())
        projectionFacts(projected)
        val entityForms =
            JSONArray(
                fixture.getJSONArray("entityForms").objects().map { sample ->
                    val input = sample.getString("input")
                    json("input" to input, "forms" to JSONArray(UiSelector.forms(input)))
                },
            )
        val limits =
            json(
                "MESSAGE_BYTES" to Limits.MESSAGE_BYTES,
                "JSON_UTF8_BYTES" to Limits.JSON_UTF8_BYTES,
                "CHUNK_BYTES" to Limits.CHUNK_BYTES,
                "TREE_BYTES" to Limits.TREE_BYTES,
                "IMAGE_BYTES" to Limits.IMAGE_BYTES,
                "OUTPUT_BYTES" to Limits.OUTPUT_BYTES,
                "MAX_RUN_MS" to Limits.MAX_RUN_MS,
                "PAIRING_NAME_UNITS" to Pairings.PAIRING_NAME_UNITS,
                "HELPER_PAYLOAD_BYTES" to HELPER_PAYLOAD_BYTES,
            )
        return json(
            "raw" to fixture.getJSONObject("raw"),
            "projected" to projected,
            "captures" to captures(projected),
            "compoundResults" to compoundResults(),
            "actionResults" to actionResults(),
            "waitResults" to waitResults(),
            "output" to outputChunks(),
            "shellCleanupFailure" to shellCleanupFailure(),
            "fileFailures" to fileFailures(),
            "vocabulary" to selectorVocabulary(),
            "entityForms" to entityForms,
            "limits" to limits,
            "queryMethods" to JSONArray(RpcMethods.QUERY_METHODS.sorted()),
        )
    }

    private fun exportCandidate(
        produced: JSONObject,
        path: String,
    ): Nothing {
        File(path).writeText(produced.toString(2))
        throw AssertionError(
            "Contract candidate exported to ${File(path).absolutePath}; the reviewed contract was not validated. " +
                "Unset ANDROIDCTL_CONTRACT_OUTPUT and rerun the tests.",
        )
    }

    private fun compareContract(
        fixture: JSONObject,
        produced: JSONObject,
    ) {
        assertEquals("Contract sections differ from the reviewed contract", fields(fixture), fields(produced))
        assertEquals(
            "Device projection differs from the reviewed contract",
            structure(fixture.getJSONObject("projected")),
            structure(produced.getJSONObject("projected")),
        )
        compareCaptures(fixture.getJSONArray("captures"), produced.getJSONArray("captures"))
        for (section in listOf("compoundResults", "actionResults", "waitResults")) {
            compareNamedSamples(section, fixture.getJSONArray(section), produced.getJSONArray(section))
        }
        for (section in listOf("output", "shellCleanupFailure")) {
            assertEquals(section, structure(fixture.getJSONObject(section)), structure(produced.getJSONObject(section)))
        }
        compareNamedSamples("fileFailures", fixture.getJSONArray("fileFailures"), produced.getJSONArray("fileFailures"))
        assertEquals(structure(fixture.getJSONObject("vocabulary")), structure(produced.getJSONObject("vocabulary")))
        val savedForms = indexSamples("entityForms reviewed", fixture.getJSONArray("entityForms"), "input")
        val actualForms = indexSamples("entityForms produced", produced.getJSONArray("entityForms"), "input")
        assertEquals("entityForms inputs differ from the reviewed contract", savedForms.keys, actualForms.keys)
        for ((input, sample) in actualForms) {
            fun forms(value: JSONObject): Set<String> =
                value.getJSONArray("forms").let { values -> (0 until values.length()).map { values.getString(it) }.toSet() }
            assertEquals(input, forms(savedForms.getValue(input)), forms(sample))
        }
        assertEquals(
            "Shared limits differ from the reviewed contract",
            structure(fixture.getJSONObject("limits")),
            structure(produced.getJSONObject("limits")),
        )
        assertEquals(
            "Query methods differ from the reviewed contract",
            structure(fixture.getJSONArray("queryMethods")),
            structure(produced.getJSONArray("queryMethods")),
        )
    }

    private fun indexSamples(
        section: String,
        samples: JSONArray,
        key: String = "name",
    ): Map<String, JSONObject> {
        val names = mutableSetOf<String>()
        val objects = samples.objects()
        for (sample in objects) {
            val name = sample.getString(key)
            assertTrue("$section contains duplicate $key: $name", names.add(name))
        }
        return objects.associateBy { it.getString(key) }
    }

    private fun compareNamedSamples(
        section: String,
        reviewed: JSONArray,
        produced: JSONArray,
    ) {
        val saved = indexSamples("$section reviewed", reviewed)
        val actual = indexSamples("$section produced", produced)
        assertEquals("$section sample names differ from the reviewed contract", saved.keys, actual.keys)
        for ((name, sample) in actual) {
            assertEquals("$section/$name differs from the reviewed contract", structure(saved.getValue(name)), structure(sample))
        }
    }

    private fun compareCaptures(
        reviewed: JSONArray,
        actual: JSONArray,
    ) {
        val golden = indexSamples("captures reviewed", reviewed)
        val current = indexSamples("captures produced", actual)
        assertEquals("captures sample names differ from the reviewed contract", golden.keys, current.keys)
        for ((name, produced) in current) {
            val saved = golden.getValue(name)
            assertEquals(fields(saved), fields(produced))
            assertEquals(saved.getString("kind"), produced.getString("kind"))
            assertEquals(structure(saved.getJSONObject("expect")), structure(produced.getJSONObject("expect")))
            val encoding = produced.getJSONObject("expect").getString("encoding")
            assertEquals(
                encoding,
                produced
                    .getJSONArray("chunks")
                    .getJSONObject(0)
                    .getJSONObject("data")
                    .getString("encoding"),
            )
            assertEquals(
                encoding,
                saved
                    .getJSONArray("chunks")
                    .getJSONObject(0)
                    .getJSONObject("data")
                    .getString("encoding"),
            )
            val currentBytes = decode(produced)
            val savedBytes = decode(saved)
            if (produced.getString("kind") == "tree") {
                assertEquals(
                    structure(JSONObject(savedBytes.toString(Charsets.UTF_8))),
                    structure(JSONObject(currentBytes.toString(Charsets.UTF_8))),
                )
            } else {
                assertArrayEquals(savedBytes, currentBytes)
                assertEquals(
                    structure(
                        saved
                            .getJSONArray("chunks")
                            .getJSONObject(0)
                            .getJSONObject("data")
                            .getJSONObject("image"),
                    ),
                    structure(
                        produced
                            .getJSONArray("chunks")
                            .getJSONObject(0)
                            .getJSONObject("data")
                            .getJSONObject("image"),
                    ),
                )
            }
            assertEquals(
                fields(saved.getJSONArray("chunks").getJSONObject(0).getJSONObject("data")),
                fields(produced.getJSONArray("chunks").getJSONObject(0).getJSONObject("data")),
            )
        }
    }

    private fun shellCleanupFailure(): JSONObject {
        val protocol = ShellProtocol("j-cleanup", OutputStore { 0L }) { }
        protocol.sent = true
        val execution =
            json(
                "event" to "foreground",
                "created" to true,
                "cancelled" to false,
                "reason" to "finished",
                "domainEmpty" to true,
                "rootDone" to true,
                "exitCode" to 7,
                "signal" to 0,
                "signalOK" to 0,
                "signalFailed" to 0,
                "cleanupUnknown" to false,
                "startupUnknown" to false,
                "startupStage" to 0,
                "startupErrno" to 0,
            )
        protocol.accept(ShellFrame('S', execution.toString().toByteArray()))
        val cleanup = json("event" to "helperEnd", "cleanupUnknown" to true, "signalFailed" to 0)
        protocol.accept(ShellFrame('S', cleanup.toString().toByteArray()))
        protocol.ended()
        return protocol.view(ended = true)
    }

    private fun selectorVocabulary(): JSONObject {
        val samples =
            listOf(
                json("editable" to true) to Role.TEXTBOX,
                json("checkable" to true, "className" to "android.widget.CheckBox") to Role.CHECKBOX,
                json("checkable" to true, "className" to "android.widget.RadioButton") to Role.RADIO,
                json("checkable" to true, "className" to "android.widget.Switch") to Role.SWITCH,
                json("className" to "android.widget.Button") to Role.BUTTON,
                json("collectionInfo" to json()) to Role.LIST,
                json("collectionItemInfo" to json()) to Role.ITEM,
                json("scrollable" to true) to Role.SCROLL,
                json("className" to "android.widget.ImageView") to Role.IMAGE,
                json() to Role.TEXT,
            )
        for ((node, expected) in samples) assertEquals(expected, UiSemantics.role(node))
        val roles = Role.entries.map { it.wire }.sorted()
        val actions = UiAction.entries.map { it.wire }.sorted()
        UiAction.entries.forEach { assertTrue(it.platformId > 0) }
        return json("roles" to JSONArray(roles), "actions" to JSONArray(actions))
    }

    private fun fields(value: JSONObject): Set<String> = value.keys().asSequence().toSet()

    private fun projectionFacts(tree: JSONObject) {
        val nodes = tree.getJSONArray("nodes").objects().associateBy { it.getString("ref") }
        assertEquals("Pay & finish", nodes.getValue("e1").getJSONObject("view").getString("name"))
        assertEquals("e1", nodes.getValue("e2").getJSONObject("view").getString("selectionTarget"))
        assertTrue(nodes.getValue("e4").getJSONObject("row").getBoolean("complete"))
        assertEquals(
            -1,
            nodes
                .getValue("e4")
                .getJSONObject("row")
                .getJSONObject("nearest")
                .getInt("row"),
        )
        assertFalse(nodes.getValue("e5").getJSONObject("view").getBoolean("nameComplete"))
        assertEquals("mixed", nodes.getValue("e3").getString("checked"))
        assertEquals("triState", nodes.getValue("e3").getString("checkedModel"))
        assertFalse(nodes.getValue("e3").getBoolean("refAvailable"))
        assertTrue(tree.getBoolean("partial"))
    }

    private fun captures(projected: JSONObject): JSONArray {
        val sessions = Sessions({ 0L }, {})
        val owner = SessionOwner("pair", "task", "host")
        val lease = sessions.acquire(owner)
        val delivery = UiDelivery(sessions, "contract")
        val result = JSONArray()
        var capture = delivery.createCapture(lease, UiDelivery.prepareTree(projected), true)
        result.put(
            read(delivery, owner, capture, "tree", multiple = false)
                .put("name", "projected-tree")
                .put("expect", json("encoding" to "zlib", "tree" to "projected")),
        )
        // Synthetic image bytes exercise raw transport; PNG rendering is a device acceptance check.
        val image = ByteArray(Limits.CHUNK_BYTES + 1).also { Random(17).nextBytes(it) }
        delivery.image(
            lease,
            capture,
            image,
            json(
                "id" to "synthetic-image",
                "width" to 1,
                "height" to 1,
                "at" to 0,
                "mappingKnown" to false,
                "mappingError" to "Synthetic transport sample",
                "shape" to null,
            ),
        )
        result.put(
            read(delivery, owner, capture, "image", multiple = true)
                .put("name", "raw-image")
                .put("expect", json("encoding" to "raw", "bytes" to image.size)),
        )
        val large = copy(projected)
        val noise = ByteArray(Limits.CHUNK_BYTES + 1).also { Random(23).nextBytes(it) }
        large.getJSONArray("nodes").getJSONObject(0).put("stateDescription", Base64.getEncoder().encodeToString(noise))
        capture = delivery.createCapture(lease, UiDelivery.prepareTree(large), false)
        result.put(
            read(delivery, owner, capture, "tree", multiple = true)
                .put("name", "large-tree")
                .put("expect", json("encoding" to "zlib", "tree" to "projected-with-noise")),
        )
        capture = delivery.createCapture(lease, UiDelivery.prepareTree(json("sequence" to 9)), false)
        result.put(
            read(delivery, owner, capture, "tree", multiple = false)
                .put("name", "minimal-tree")
                .put("expect", json("encoding" to "raw", "tree" to "minimal")),
        )
        return result
    }

    private fun compoundResults(): JSONArray {
        val target = node("e7", "Search").put("editable", true).put("focused", true).put("isMultiLine", false)
        target.put("actions", JSONArray(listOf("fill", "ime")))
        UiSemantics.project(listOf(target))

        fun sample(
            name: String,
            submitField: JSONObject? = null,
            blockedSubmit: Boolean = false,
        ): JSONObject {
            val args = json("ref" to "e7", "value" to "hello")
            val submitRequested = submitField != null || blockedSubmit
            if (submitRequested) args.put("submit", true)
            val progress = FillProgress(target, submitRequested)
            val run = contractRun("fill", args)
            run.resolve("original", target)
            for ((step, action) in listOf(FillStep.FOCUS to UiAction.FOCUS, FillStep.FILL to UiAction.FILL)) {
                if (!blockedSubmit) {
                    run.begin(progress.pending(step, "${step.command} result pending"))
                    progress.started(step)
                }
                val stepResult = outcome(step.command, data = UiActions.stepData(action, target))
                val result =
                    if (step == FillStep.FILL) {
                        UiVerification.verifiedResult(stepResult, Verification(VerificationState.MATCHES))
                    } else {
                        stepResult
                    }
                progress.record(step, result)
            }
            if (blockedSubmit) {
                progress.fail(FillStep.SUBMIT, Rejected("staleRef", "Original field is stale"))
            } else if (submitField != null) {
                run.begin(progress.pending(FillStep.SUBMIT, "Submit result pending"))
                progress.started(FillStep.SUBMIT)
                progress.record(FillStep.SUBMIT, outcome("submit", data = json("selected" to UiActions.submitSelection(submitField))))
            }
            run.remember(progress.result(run.dispatchStarted))
            val result = run.response()
            val expected = if (blockedSubmit) Outcome.NOT_SENT else Outcome.DONE
            assertEquals(expected, result.getString("result"))
            val data = result.getJSONObject("data")
            assertEquals(structure(progress.step(FillStep.FOCUS)), structure(data.getJSONObject("focus")))
            assertEquals(structure(progress.step(FillStep.FILL)), structure(data.getJSONObject("fill")))
            assertEquals(submitRequested, data.has("submit"))
            if (submitRequested) assertEquals(structure(progress.step(FillStep.SUBMIT)), structure(data.getJSONObject("submit")))
            return json(
                "name" to name,
                "expect" to expected,
                "request" to json("command" to run.command, "args" to copy(run.args)),
                "response" to result,
            )
        }

        val enterField = copy(target).put("actions", JSONArray(listOf("fill")))
        return JSONArray(
            listOf(
                sample("ordinary-write"),
                sample("no-op-fill-submit-not-sent", blockedSubmit = true),
                sample("ime-done", submitField = target),
                sample("enter-done", submitField = enterField),
            ),
        )
    }

    private fun contractRun(
        command: String,
        args: JSONObject,
        budget: UiBudget = UiBudget(5000) { 0 },
    ): UiRun = UiRun(command, Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS), args, budget)

    private fun waitResults(): JSONArray {
        fun tree(
            target: JSONObject,
            sequence: Int = 1,
        ): JSONObject {
            UiSemantics.project(listOf(target))
            return json(
                "sequence" to sequence,
                "partial" to false,
                "connection" to 1,
                "scope" to json("window" to 1),
                "windows" to JSONArray(),
                "nodes" to JSONArray(listOf(target)),
            )
        }
        val ready = node("e1", "Ready")
        val loading = node("e1").put("className", "android.widget.ProgressBar")
        val gone = json("gone" to true, "selector" to json("text" to "Saving"))
        val results =
            JSONArray(
                listOf(
                    Triple("gone-absent", gone, listOf(tree(ready))),
                    Triple("idle-loading", json("idle" to true), listOf(tree(loading), tree(copy(loading), sequence = 2))),
                ).map { (name, args, observations) ->
                    val sessions = Sessions({ 0L }, {})
                    val delivery = UiDelivery(sessions, "contract-worker")
                    val lease = sessions.acquire(SessionOwner("pair", "task", "host"))
                    val wait = UiWait(args)
                    val run = contractRun("wait", args)
                    run.remember(wait.start(delivery, lease, null))
                    for (tree in observations) run.remember(wait.observe(tree))
                    json(
                        "name" to name,
                        "request" to json("command" to "wait", "args" to copy(args)),
                        "response" to run.response(),
                    )
                },
            )
        for (name in listOf(
            "screen-change-cross-window",
            "screen-change-scope-mismatch",
        )) {
            val sessions = Sessions({ 0L }, {})
            val delivery = UiDelivery(sessions, "worker")
            val lease = sessions.acquire(SessionOwner("pair", "task", "host"))
            val args =
                json(
                    "screenChange" to true,
                    "scope" to if (name == "screen-change-scope-mismatch") json("window" to 1) else JSONObject(),
                )
            val run = UiRun("wait", lease, args, UiBudget(5000) { 0 })
            val wait = UiWait(args)
            val before =
                tree(node("e1", "Before"))
                    .put("selectionScope", JSONObject())
                    .put("scope", json("window" to 1, "package" to "fixture"))
            delivery.createCapture(lease, UiDelivery.prepareTree(before), false)
            val initial = wait.start(delivery, lease, before)
            run.remember(initial)
            if (initial.getString("result") != Outcome.NOT_SENT) {
                val after =
                    tree(node("e1", "After").put("windowId", 2), sequence = 2)
                        .put("selectionScope", JSONObject())
                        .put("scope", json("window" to 2, "package" to "other"))
                run.remember(wait.observe(after))
            }
            results.put(
                json(
                    "name" to name,
                    "request" to json("command" to "wait", "args" to args),
                    "response" to run.response(),
                ),
            )
        }
        return results
    }

    private fun actionResults(): JSONArray {
        val target = node("e7", "Search").put("clickable", true).put("className", "android.widget.Button")
        UiSemantics.project(listOf(target))

        fun sample(
            name: String,
            run: UiRun,
            result: JSONObject? = null,
            expired: Boolean = false,
        ): JSONObject {
            result?.let(run::remember)
            return json(
                "name" to name,
                "request" to json("command" to run.command, "args" to copy(run.args)),
                "response" to run.response(expired),
            )
        }
        val click = contractRun("click", json("ref" to "e7"))
        click.resolve("original", target)
        click.select("click", target)
        click.begin(outcome("click", Outcome.UNKNOWN, "Platform action result pending"))
        val selector = contractRun("click", json("selector" to json("name" to "Search")))
        selector.resolve("selector", target)
        selector.select("click", target)
        selector.begin(null)
        val tap = contractRun("tap", json("point" to JSONArray(listOf(10, 20))))
        tap.select("tap")
        tap.begin(null)
        return JSONArray(
            listOf(
                sample("plain-click", click, outcome("click")),
                sample("selector-click", selector, outcome("click")),
                sample("coordinate-pending-at-expiry", tap, expired = true),
                rowResult(),
                scrollResult(),
            ),
        )
    }

    private fun scrollResult(): JSONObject {
        val target =
            node("e30", "Results")
                .put("className", "androidx.recyclerview.widget.RecyclerView")
                .put("scrollable", true)
                .put("actions", JSONArray(listOf("page-down")))
        UiSemantics.project(listOf(target))
        assertTrue(UiAction.SCROLL_DOWN.ready(target))
        val route = requireNotNull(UiScroll.route(target, UiAction.SCROLL_DOWN))
        var now = 0L
        val run = contractRun("scroll", json("ref" to "e30", "direction" to "down"), UiBudget(5000) { now })
        run.resolve("original", target)
        run.select(UiSemantics.actionName(route), target)
        now = 5000L
        try {
            run.budget.check()
        } catch (error: Exception) {
            run.remember(run.failure(error))
        }
        return json(
            "name" to "scroll-page-not-sent",
            "request" to json("command" to run.command, "args" to copy(run.args)),
            "response" to run.response(),
        )
    }

    private fun rowResult(): JSONObject {
        val row =
            node("e20", "Option")
                .put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
                .put("refAvailable", true)
                .put("actions", JSONArray(listOf("click")))
        val label =
            node("e12", "Option", "e20")
                .put("refAvailable", true)
                .put("packageName", "example.options")
                .put("resourceId", "example.options:id/label")
                .put("bounds", JSONArray(listOf(0, 0, 100, 40)))
        val control = node("e13", parent = "e20").put("refAvailable", true).put("checkable", true)
        val nodes = listOf(row, label, control)
        UiSemantics.project(nodes)
        val tree = json("partial" to false, "nodes" to JSONArray(nodes))
        val source =
            copy(label)
                .put("ref", "e7")
                .put("sourceId", "old-label")
                .put("sourceContext", "worker:3")
                .put("sourceCapture", "worker:8")
        UiRefRepair.requireSource(source, "e7", "worker:3")
        val repaired = UiRefRepair.select(source, tree)
        val selection = UiAdvice.suggestedRow(tree, repaired, UiAction.CHECK)!!
        val selectedRow = selection.row
        val verifiedControl = selection.verificationControl
        val run = contractRun("check", json("ref" to "e7", "sourceSignature" to source, "repairRef" to true))
        run.resolve("signature", repaired)
        run.select("click", selectedRow)
        val data = json("verificationTarget" to json("ref" to verifiedControl.getString("ref")))
        run.begin(outcome("check", Outcome.UNKNOWN, "Suggested row click pending", copy(data)))
        val result =
            UiVerification.verifiedResult(
                outcome("check", data = data),
                UiVerification.evaluate(UiAction.CHECK, run.args, copy(verifiedControl).put("checked", "true")),
            )
        run.remember(result)
        return json(
            "name" to "suggested-row-matches",
            "request" to json("command" to run.command, "args" to copy(run.args)),
            "response" to run.response(),
        )
    }

    private fun outputChunks(): JSONObject {
        val store = OutputStore { 0L }
        val job = "jcontract"
        val stdout = ByteArray(Limits.CHUNK_BYTES + 7) { (it % 251).toByte() }
        val stderr = "diagnostic\n".toByteArray()
        store.add(job, CapturedStream(stdout, stdout.size.toLong(), false), CapturedStream(stderr, stderr.size.toLong(), false))
        val chunks = JSONObject()
        for ((stream, bytes) in listOf("stdout" to stdout, "stderr" to stderr)) {
            val responses = JSONArray()
            val received = ByteArrayOutputStream()
            do {
                val data = store.read(job, json("stream" to stream, "offset" to received.size()))
                assertEquals(job, data.getString("job"))
                assertEquals(received.size(), data.getInt("offset"))
                val block = Base64.getDecoder().decode(data.getString("bytes"))
                assertTrue(block.size in 1..Limits.CHUNK_BYTES)
                received.write(block)
                responses.put(outcome("job output", data = data))
            } while (received.size() < bytes.size)
            assertArrayEquals(bytes, received.toByteArray())
            chunks.put(stream, responses)
        }
        return json("job" to job, "metadata" to store.describe(job), "chunks" to chunks)
    }

    private fun fileFailures(): JSONArray {
        val beforeOpen = fileFailure("pull", "f1", false, false, false, Rejected("sessionChanged", "Lease changed"))
        val readFailed = fileFailure("pull", "f2", true, true, false, IOException("Read interrupted"))
        val publicationUnknown = fileFailure("push", "f3", true, true, true, IOException("Publication interrupted"))
        val rejected = fileFailure("push", "f4", true, true, true, Rejected("publishFailed", "Target exists"))
        return JSONArray(
            listOf(
                json("name" to "before-open", "expect" to Outcome.NOT_SENT, "response" to beforeOpen),
                json("name" to "read-failed", "expect" to Outcome.FAILED, "response" to readFailed),
                json("name" to "publication-unknown", "expect" to Outcome.UNKNOWN, "response" to publicationUnknown),
                json("name" to "publication-rejected", "expect" to Outcome.FAILED, "response" to rejected),
            ),
        )
    }

    private fun read(
        delivery: UiDelivery,
        owner: SessionOwner,
        capture: String,
        kind: String,
        multiple: Boolean,
    ): JSONObject {
        val chunks = JSONArray()
        var offset = 0
        do {
            val response = delivery.read(owner, json("capture" to capture, "kind" to kind, "offset" to offset))
            chunks.put(response)
            val data = response.getJSONObject("data")
            assertEquals(capture, data.getString("capture"))
            offset += Base64.getDecoder().decode(data.getString("bytes")).size
        } while (offset < data.getInt("size"))
        return json("kind" to kind, "multiple" to multiple, "chunks" to chunks).also(::decode)
    }

    private fun decode(capture: JSONObject): ByteArray {
        val chunks = capture.getJSONArray("chunks").objects()
        val first = chunks.first().getJSONObject("data")
        val multiple = capture.getBoolean("multiple")
        assertEquals(multiple, chunks.size > 1)
        val output = ByteArrayOutputStream()
        for (chunk in chunks) {
            assertEquals(setOf("command", "result", "reason", "data"), fields(chunk))
            assertEquals("ui.read", chunk.getString("command"))
            assertEquals("done", chunk.getString("result"))
            val data = chunk.getJSONObject("data")
            assertEquals("available", data.getString("state"))
            assertEquals(fields(first), fields(data))
            assertEquals(output.size(), data.getInt("offset"))
            for (field in listOf("capture", "state", "size", "sha256", "encoding", "decodedSize", "decodedSha256")) {
                assertEquals(first.get(field), data.get(field))
            }
            val bytes = Base64.getDecoder().decode(data.getString("bytes"))
            assertTrue(bytes.size in 1..Limits.CHUNK_BYTES)
            if (multiple) {
                assertEquals(
                    if (chunk ===
                        chunks.last()
                    ) {
                        first.getInt("size") % Limits.CHUNK_BYTES
                    } else {
                        Limits.CHUNK_BYTES
                    },
                    bytes.size,
                )
            }
            output.write(bytes)
        }
        val encoded = output.toByteArray()
        assertEquals(first.getInt("size"), encoded.size)
        assertEquals(first.getString("sha256"), sha256(encoded))
        val encoding = first.getString("encoding")
        assertTrue(encoding in if (capture.getString("kind") == "image") setOf("raw") else setOf("raw", "zlib"))
        val decoded = if (encoding == "raw") encoded else InflaterInputStream(encoded.inputStream()).use { it.readBytes() }
        assertEquals(first.getInt("decodedSize"), decoded.size)
        assertEquals(first.getString("decodedSha256"), sha256(decoded))
        return decoded
    }
}
