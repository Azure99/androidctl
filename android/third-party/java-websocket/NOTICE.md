# Java-WebSocket 1.6.0

Source: [v1.6.0](https://github.com/TooTallNate/Java-WebSocket/tree/da3cf2a777aed862f2f5b5cf060cae7969958667),
commit `da3cf2a777aed862f2f5b5cf060cae7969958667`.

The two vendored sources retain their upstream MIT license headers. AndroidCtl
patches wrapped-channel pending writes and drains existing TLS ciphertext before
wrapping new plaintext. The build replaces these two classes in the 1.6.0 JAR;
the unmodified dependency is not included in the APK alongside the patched JAR.

SSLSocketChannel2 also limits handshake wrapping to NEED_WRAP with no pending
delegated tasks, preserves unsent ciphertext when FINISHED resizes buffers, and
keeps selector write interest only for pending output or handshake work. Pending
application writes regain write interest after NEED_UNWRAP. The upstream
delegated-task Future mechanism is retained without a separate wakeup framework.

Local changes are marked `androidctl:` at the affected blocks.
When updating the dependency, compare the marked
blocks against this fixed commit and verify pending-write and TLS handshake behavior.
