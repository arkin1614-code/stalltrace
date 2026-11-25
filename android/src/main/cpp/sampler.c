#define _GNU_SOURCE
// Foreground platform-thread sampler for the ARM64 Android processes.
#include <errno.h>
#include <fcntl.h>
#include <jni.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

enum { MAX_FRAMES = 48 };
typedef struct {
    int64_t when_ns;
    int count;
    int stop; // 0 end, 1 invalid/missing FP, 2 unreadable, 3 limit
    uintptr_t pc[MAX_FRAMES];
} Snapshot;
_Static_assert(sizeof(Snapshot) <= PIPE_BUF, "one atomic pipe write");

static int scratch[2], result[2], sample_signal;
static uintptr_t stack_low, stack_high;
static pid_t main_tid;
static pthread_once_t initialized = PTHREAD_ONCE_INIT;
static int available;
static int64_t mono_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}

static void capture(int sig, siginfo_t *info, void *context) {
    int saved_errno = errno;
    if (sig == sample_signal && info->si_code == SI_TKILL &&
        info->si_pid == getpid() && gettid() == main_tid) {
        const mcontext_t *m = &((ucontext_t *)context)->uc_mcontext;
        Snapshot s;
        s.when_ns = mono_ns();
        s.count = 2;
        s.stop = 0;
        s.pc[0] = m->pc;
        s.pc[1] = m->regs[30]; // independent LR evidence; offline CFI decides whether it is a caller
        uintptr_t fp = m->regs[29], lower = m->sp;
        while (fp && s.count < MAX_FRAMES) {
            uintptr_t pair[2]; // caller FP, saved return address
            if ((fp & 7) || fp < lower || fp < stack_low || fp > stack_high - sizeof(pair)) {
                s.stop = 1;
                break;
            }
            // Copy via kernel EFAULT checking. Never dereference an untrusted FP.
            // The scratch pipe has exactly one user on this thread, no nested sampling.
            if (write(scratch[1], (const void *)fp, sizeof(pair)) != sizeof(pair) ||
                read(scratch[0], pair, sizeof(pair)) != sizeof(pair)) {
                s.stop = 2;
                break;
            }
            if (!pair[1]) break;
            s.pc[s.count++] = pair[1];
            lower = fp + sizeof(pair);
            fp = pair[0];
        }
        if (s.count == MAX_FRAMES) s.stop = 3;
        // Only initialized fields leave the handler. All pipe operations are nonblocking.
        (void)write(result[1], &s, offsetof(Snapshot, pc) + s.count * sizeof(uintptr_t));
    }
    errno = saved_errno;
}

static void install(void) {
    if (gettid() != getpid()) return;
    main_tid = gettid();
    pthread_attr_t attr;
    if (pthread_getattr_np(pthread_self(), &attr)) return;
    void *base;
    size_t size;
    int ok = pthread_attr_getstack(&attr, &base, &size);
    pthread_attr_destroy(&attr);
    if (ok || size < 16) return;
    stack_low = (uintptr_t)base;
    stack_high = stack_low + size;
    sample_signal = SIGRTMIN + 6; // must be reserved by the integrating application
    struct sigaction old, action = {0};
    sigset_t mask;
    pthread_sigmask(SIG_SETMASK, NULL, &mask);
    if (sample_signal > SIGRTMAX || sigismember(&mask, sample_signal) ||
        sigaction(sample_signal, NULL, &old) || old.sa_handler != SIG_DFL) return;
    if (pipe2(scratch, O_CLOEXEC | O_NONBLOCK)) return;
    if (pipe2(result, O_CLOEXEC | O_NONBLOCK)) {
        close(scratch[0]); close(scratch[1]); return;
    }
    action.sa_sigaction = capture;
    sigemptyset(&action.sa_mask);
    action.sa_flags = SA_SIGINFO | SA_RESTART | SA_ONSTACK;
    if (sigaction(sample_signal, &action, NULL)) {
        close(scratch[0]); close(scratch[1]); close(result[0]); close(result[1]); return;
    }
    available = 1;
}

JNIEXPORT jboolean JNICALL
Java_dev_stalltrace_AnrMonitor_installSampler(JNIEnv *env, jclass type) {
    (void)env; (void)type;
    pthread_once(&initialized, install);
    return available;
}

JNIEXPORT jstring JNICALL
Java_dev_stalltrace_AnrMonitor_sample(JNIEnv *env, jclass type) {
    (void)type;
    if (!available) return NULL;
    // Called only by the process-wide monitor. Validate actual capture time.
    struct sigaction current;
    if (sigaction(sample_signal, NULL, &current) || !(current.sa_flags & SA_SIGINFO) ||
        current.sa_sigaction != capture) return NULL;
    Snapshot s;
    while (read(result[0], &s, sizeof(s)) > 0) {}
    int64_t start = mono_ns();
    if (syscall(SYS_tgkill, getpid(), main_tid, sample_signal)) return NULL;
    struct pollfd p = {.fd = result[0], .events = POLLIN};
    if (poll(&p, 1, 120) != 1) return NULL;
    ssize_t n = read(result[0], &s, sizeof(s));
    if (n < (ssize_t)offsetof(Snapshot, pc) || s.when_ns < start ||
        s.count < 1 || s.count > MAX_FRAMES ||
        (size_t)n != offsetof(Snapshot, pc) + s.count*sizeof(uintptr_t)) return NULL;

    char text[1600];
    int used = snprintf(text, sizeof(text), "%lld %d %d\n", (long long)s.when_ns, main_tid, s.stop);
    for (int i = 0; i < s.count; ++i) {
        register uintptr_t address __asm__("x30") = s.pc[i];
        __asm__ volatile("hint #7" : "+r"(address));
        if (i && address >= 4) address -= 4;
        used += snprintf(text + used, sizeof(text) - used, "%s %lx\n",
                i == 0 ? "pc" : (i == 1 ? "lr" : "call"), (unsigned long)address);
    }
    return (*env)->NewStringUTF(env, text);
}

// Bind before monitoring starts. Lazy JNI lookup can itself wait on a later dlopen.
JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void)reserved;
    JNIEnv *env;
    if ((*vm)->GetEnv(vm, (void **)&env, JNI_VERSION_1_6) != JNI_OK) return JNI_ERR;
    jclass monitor = (*env)->FindClass(env, "dev/stalltrace/AnrMonitor");
    if (!monitor) return JNI_ERR;
    const JNINativeMethod methods[] = {
        {"installSampler", "()Z", (void *)Java_dev_stalltrace_AnrMonitor_installSampler},
        {"sample", "()Ljava/lang/String;", (void *)Java_dev_stalltrace_AnrMonitor_sample}
    };
    if ((*env)->RegisterNatives(env, monitor, methods, 2) != JNI_OK) return JNI_ERR;
    return JNI_VERSION_1_6;
}
