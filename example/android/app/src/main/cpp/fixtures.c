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
