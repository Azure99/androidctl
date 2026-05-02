package com.rainng.androidctl.agent.events

object DeviceEventHub {
    private val lifecycleLock = Any()
    private var cooldownSchedulerFactory: () -> CooldownScheduler = { ExecutorCooldownScheduler() }
    private var processorFactory: (CooldownScheduler) -> DeviceEventProcessor = ::createDefaultProcessor
    private var processor: DeviceEventProcessor? = null

    fun recordRuntimeStatus(payload: RuntimeStatusPayload) {
        synchronized(lifecycleLock) {
            activeProcessorLocked().recordRuntimeStatus(payload)
        }
    }

    internal fun recordAccessibilityObservation(
        event: ObservedAccessibilityEvent,
        environment: DeviceEventEnvironment,
    ) {
        val activeProcessor =
            synchronized(lifecycleLock) {
                activeProcessorLocked()
            }
        activeProcessor.recordAccessibilityEvent(
            event = event,
            environment = environment,
        )
    }

    fun poll(request: EventPollRequest): EventPollResult {
        val activeProcessor =
            synchronized(lifecycleLock) {
                activeProcessorLocked()
            }
        return activeProcessor.poll(request)
    }

    fun cancelPendingWork() {
        synchronized(lifecycleLock) {
            processor?.cancelPendingWork()
        }
    }

    fun resetForAttachmentChange() {
        synchronized(lifecycleLock) {
            activeProcessorLocked().resetForAttachmentChange()
        }
    }

    fun shutdown() {
        closeCurrentProcessor()
    }

    internal fun resetForTest() {
        closeCurrentProcessor()
        synchronized(lifecycleLock) {
            cooldownSchedulerFactory = { ExecutorCooldownScheduler() }
            processorFactory = ::createDefaultProcessor
        }
    }

    internal fun configureForTest(
        cooldownSchedulerFactory: () -> CooldownScheduler = { ExecutorCooldownScheduler() },
        processorFactory: (CooldownScheduler) -> DeviceEventProcessor = ::createDefaultProcessor,
    ) {
        closeCurrentProcessor()
        synchronized(lifecycleLock) {
            this.cooldownSchedulerFactory = cooldownSchedulerFactory
            this.processorFactory = processorFactory
        }
    }

    private fun activeProcessorLocked(): DeviceEventProcessor =
        processor
            ?: processorFactory(cooldownSchedulerFactory())
                .also { processor = it }

    private fun closeCurrentProcessor() {
        val processorToClose =
            synchronized(lifecycleLock) {
                processor.also { processor = null }
            }
        processorToClose?.close()
    }

    private fun createDefaultProcessor(cooldownScheduler: CooldownScheduler): DeviceEventProcessor =
        DeviceEventProcessor(cooldownScheduler = cooldownScheduler)
}
