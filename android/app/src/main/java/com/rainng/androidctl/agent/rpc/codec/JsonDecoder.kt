package com.rainng.androidctl.agent.rpc.codec

internal interface JsonDecoder<T> {
    fun read(reader: JsonReader): T
}
