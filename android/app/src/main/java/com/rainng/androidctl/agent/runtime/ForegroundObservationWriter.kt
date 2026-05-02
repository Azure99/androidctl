package com.rainng.androidctl.agent.runtime

internal interface ForegroundObservationWriter {
    fun recordObservedWindowState(
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
    )

    fun reset()
}

internal class GraphForegroundObservationWriter(
    private val recordObservedWindowStateAction: (Int, String?, String?) -> Unit,
    private val resetAction: () -> Unit,
) : ForegroundObservationWriter {
    override fun recordObservedWindowState(
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
    ) {
        recordObservedWindowStateAction(eventType, packageName, windowClassName)
    }

    override fun reset() {
        resetAction()
    }
}
