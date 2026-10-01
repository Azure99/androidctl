package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject

internal fun node(
    ref: String,
    text: String = "",
    parent: String? = null,
): JSONObject =
    json(
        "ref" to ref,
        "parent" to parent,
        "sourceId" to ref,
        "windowId" to 1,
        "bounds" to JSONArray(listOf(0, 0, 100, 100)),
        "className" to "android.widget.TextView",
        "text" to text,
        "description" to null,
        "hint" to null,
        "visible" to true,
        "enabled" to true,
        "editable" to false,
        "clickable" to false,
        "longClickable" to false,
        "checkable" to false,
        "scrollable" to false,
        "focused" to false,
        "password" to false,
        "showingHintText" to false,
        "checked" to "false",
        "checkedModel" to "binary",
        "actions" to JSONArray(),
        "collectionInfo" to null,
        "collectionItemInfo" to null,
        "unavailable" to JSONArray(),
    )
