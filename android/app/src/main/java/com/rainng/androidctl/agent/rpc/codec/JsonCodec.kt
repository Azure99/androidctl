package com.rainng.androidctl.agent.rpc.codec

internal interface JsonCodec<T> :
    JsonDecoder<T>,
    JsonEncoder<T>
