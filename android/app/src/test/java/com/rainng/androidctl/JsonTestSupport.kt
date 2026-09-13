package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject

internal fun structure(value: Any?): Any? =
    when (value) {
        is JSONObject -> value.keys().asSequence().associateWith { structure(value.get(it)) }
        is JSONArray -> (0 until value.length()).map { structure(value.get(it)) }
        is Number -> value.toString().toBigDecimal().stripTrailingZeros()
        JSONObject.NULL -> null
        else -> value
    }
