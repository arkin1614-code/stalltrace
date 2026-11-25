#include <android/log.h>
#include <errno.h>
#include <time.h>

__attribute__((constructor,noinline)) static void fixture_slow_constructor(void) {
    __android_log_write(ANDROID_LOG_INFO, "AnrLab", "entered slow constructor");
    struct timespec left = {65, 0};
    while (nanosleep(&left, &left) != 0 && errno == EINTR) {}
    __android_log_write(ANDROID_LOG_INFO, "AnrLab", "left slow constructor");
}
