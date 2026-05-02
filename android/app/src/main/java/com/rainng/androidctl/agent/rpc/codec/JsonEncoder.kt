package com.rainng.androidctl.agent.rpc.codec

internal interface JsonEncoder<T> {
    fun write(
        writer: JsonWriter,
        value: T,
    )
}
