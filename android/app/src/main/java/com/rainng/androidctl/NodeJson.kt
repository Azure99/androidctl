package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject

internal fun JSONArray.objects(): List<JSONObject> = (0 until length()).map(::getJSONObject)

internal fun JSONObject.text(field: String): String = if (isNull(field)) "" else optString(field)

internal fun JSONObject.unavailable(vararg fields: String): Boolean =
    getJSONArray("unavailable").objects().any { it.text("field") in fields }
