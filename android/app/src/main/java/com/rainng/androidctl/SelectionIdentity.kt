package com.rainng.androidctl

internal data class RowPosition(
    val row: Int,
    val column: Int,
)

internal data class SelectionIdentity(
    val sourceId: String,
    val windowId: Int,
    val role: String,
    val name: String,
    val rowLabel: String?,
    val positions: List<RowPosition>,
)
