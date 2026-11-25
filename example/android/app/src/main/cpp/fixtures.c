#include <jni.h>
#include <errno.h>
#include <time.h>

// Synchronous FFI workload. Preserve both native frames in the release fixture.
__attribute__((noinline, visibility("default"))) void fixture_native_wait(int ms) {
    struct timespec remaining = {ms / 1000, (ms % 1000) * 1000000L};
    while (nanosleep(&remaining, &remaining) != 0 && errno == EINTR) {}
}
__attribute__((noinline, visibility("default"))) void fixture_native_entry(int ms) {
    fixture_native_wait(ms);
    __asm__ volatile("" ::: "memory");
}

JNIEXPORT void JNICALL
Java_dev_stalltrace_stalltrace_1example_MainActivity_waitOnPlatform(JNIEnv *env, jclass type) {
    (void)env; (void)type;
    fixture_native_entry(18000);
    __asm__ volatile("" ::: "memory");
}

// Exercise co-existence with an SDK that already owns the sampling signal.
#include <signal.h>
static void fixture_signal_handler(int sig) { (void)sig; }
JNIEXPORT jboolean JNICALL
Java_dev_stalltrace_stalltrace_1example_MainActivity_reserveTestSignal(JNIEnv *env, jclass type) {
    (void)env; (void)type;
    int sig = SIGRTMIN + 6;
    struct sigaction old, own = {0};
    if (sig > SIGRTMAX || sigaction(sig, NULL, &old) || old.sa_handler != SIG_DFL) return JNI_FALSE;
    own.sa_handler = fixture_signal_handler;
    sigemptyset(&own.sa_mask);
    return sigaction(sig, &own, NULL) == 0;
}
