package android.os;

// JVM tests use monotonic elapsed time; budget cutoffs retain their injected clock.
public final class SystemClock {
    private SystemClock() {}

    public static long elapsedRealtime() {
        return System.nanoTime() / 1_000_000L;
    }
}
