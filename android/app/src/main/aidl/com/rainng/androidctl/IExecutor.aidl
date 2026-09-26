package com.rainng.androidctl;

interface IExecutor {
    String call(String request) = 0;
    String status() = 1;
    String release(String workerId, long leaseId) = 2;
    String revoke(String pairingId) = 3;
    String prepareStop() = 4;
    String reconcile(String allowedPairings) = 5;
    void destroy() = 16777114;
}
