package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.errors.RequestValidationException
import java.math.BigDecimal
import java.math.BigInteger
import kotlin.math.floor

internal object JsonStrictNumberDecoder {
    private val LONG_MIN_BIG_INTEGER: BigInteger = BigInteger.valueOf(Long.MIN_VALUE)
    private val LONG_MAX_BIG_INTEGER: BigInteger = BigInteger.valueOf(Long.MAX_VALUE)

    fun asInt(
        raw: Any?,
        invalidMessage: String,
    ): Int {
        val asLong = asLong(raw, invalidMessage)
        return asLong
            .toInt()
            .takeIf { it.toLong() == asLong }
            ?: throw RequestValidationException(invalidMessage)
    }

    fun asLong(
        raw: Any?,
        invalidMessage: String,
    ): Long = decodeLong(raw as? Number ?: invalidNumber(invalidMessage), invalidMessage)

    private fun decodeLong(
        number: Number,
        invalidMessage: String,
    ): Long =
        when (number) {
            is Byte, is Short, is Int, is Long -> number.toLong()
            is BigInteger -> number.toStrictLong(invalidMessage)
            is Float, is Double, is BigDecimal -> invalidNumber(invalidMessage)
            else -> number.toStrictLongFromUnknownNumber(invalidMessage)
        }

    private fun BigInteger.toStrictLong(invalidMessage: String): Long =
        if (this < LONG_MIN_BIG_INTEGER || this > LONG_MAX_BIG_INTEGER) {
            invalidNumber(invalidMessage)
        } else {
            toLong()
        }

    private fun Number.toStrictLongFromUnknownNumber(invalidMessage: String): Long {
        val asDouble = toDouble()
        requireFiniteIntegral(asDouble, invalidMessage)
        val candidate = toLong()
        return candidate.takeIf { it.toDouble() == asDouble } ?: invalidNumber(invalidMessage)
    }

    private fun requireFiniteIntegral(
        value: Double,
        invalidMessage: String,
    ) {
        if (!value.isFinite() || floor(value) != value) {
            invalidNumber(invalidMessage)
        }
    }

    private fun invalidNumber(invalidMessage: String): Nothing = throw RequestValidationException(invalidMessage)
}
