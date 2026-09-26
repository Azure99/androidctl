/*
 * argv: deadline in CLOCK_BOOTTIME milliseconds, shell script, slot-lock path,
 * expected slot-lock device number, expected slot-lock inode number.
 * stdin accepts GO\n once to launch and CANCEL\n to request termination. EOF
 * cancels the job as a disconnected observer; deadline expiry also cancels it.
 * stdout carries atomic frames: "ACF1", one kind byte (S JSON state, O stdout,
 * E stderr), then a three-byte big-endian payload length, then <=2040 bytes.
 * Normal states: ready, foreground, outputSummary, O/E blocks, outputEnd,
 * helperEnd. signalError may interleave. foreground does not release the helper
 * slot: helperEnd requires an empty child domain. Startup can end with rejected
 * or helperError; observer loss/delivery expiry can omit output or final frames.
 */
#define _GNU_SOURCE
#include "helper_slots.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define OUTPUT_CAP (128u * 1024u)
#define READ_SLICE (16u * 1024u)
#define FRAME_CAP 2048u
#define FRAME_MAGIC "ACF1"
#define FRAME_MAGIC_BYTES 4u
#define FRAME_HEADER_BYTES 8u
#define FRAME_KIND_OFFSET 4u
#define FRAME_LENGTH_HIGH_OFFSET 5u
#define FRAME_LENGTH_MIDDLE_OFFSET 6u
#define FRAME_LENGTH_LOW_OFFSET 7u
#define FRAME_PAYLOAD_CAP (FRAME_CAP - FRAME_HEADER_BYTES)
#define WAIT_SLICE 64
#define TERM_MS 2000
#define REAP_MS 200
#define DELIVERY_POLL_MS 1
#define CLEANUP_POLL_MS 100
#define EXECUTION_POLL_MS 20
#define CHILD_SCAN_MS 40
#define PIPE_DRAIN_MS 500
#define DELIVERY_MS 2000
#define DIAGNOSTIC_CAP 64
#define CONTROL_CAP 16u
#define START_ERROR_MAGIC 0x41435445

enum startup_stage {
    START_INPUT = 1,
    START_STDIO = 2,
    START_REPORT = 3,
    START_CLOSE = 4,
    START_SIGNAL = 5,
    START_EXEC = 6,
    START_SETSID = 7,
};

enum argument {
    ARG_DEADLINE = 1,
    ARG_SCRIPT,
    ARG_SLOT_PATH,
    ARG_SLOT_DEVICE,
    ARG_SLOT_INODE,
    ARG_COUNT,
};

static bool observer_gone;
static char diagnostics[DIAGNOSTIC_CAP][1024];
static size_t diagnostic_head;
static size_t diagnostic_count;
static size_t diagnostic_dropped;

static bool frame(char kind, const void *bytes, size_t length) {
    unsigned char data[FRAME_CAP];
    if (length > FRAME_PAYLOAD_CAP)
        return false;
    memcpy(data, FRAME_MAGIC, FRAME_MAGIC_BYTES);
    data[FRAME_KIND_OFFSET] = (unsigned char)kind;
    data[FRAME_LENGTH_HIGH_OFFSET] = (unsigned char)(length >> 16);
    data[FRAME_LENGTH_MIDDLE_OFFSET] = (unsigned char)(length >> 8);
    data[FRAME_LENGTH_LOW_OFFSET] = (unsigned char)length;
    memcpy(data + FRAME_HEADER_BYTES, bytes, length);
    ssize_t sent;
    do {
        sent = write(1, data, length + FRAME_HEADER_BYTES);
    } while (sent < 0 && errno == EINTR);
    if (sent < 0 && errno != EAGAIN)
        observer_gone = true;
    return sent == (ssize_t)length + FRAME_HEADER_BYTES;
}

static bool state(const char *format, ...) __attribute__((format(printf, 1, 2)));

static bool state(const char *format, ...) {
    char data[FRAME_PAYLOAD_CAP];
    va_list args;
    va_start(args, format);
    int length = vsnprintf(data, sizeof(data), format, args);
    va_end(args);
    return length >= 0 && (size_t)length < sizeof(data) && frame('S', data, (size_t)length);
}

static void fatal(const char *stage) {
    int saved = errno;
    state("{\"event\":\"helperError\",\"stage\":\"%s\",\"errno\":%d}", stage, saved);
    _exit(90);
}

static int64_t millis(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_BOOTTIME, &value))
        fatal("clock");
    return (int64_t)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}

static bool nonblock(int fd) {
    int flags = fcntl(fd, F_GETFL);
    return flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

static void close_fd(int *fd) {
    if (*fd >= 0)
        close(*fd);
    *fd = -1;
}

static bool close_from(int first) {
    if (!syscall(SYS_close_range, (unsigned)first, ~0u, 0u))
        return true;
    if (errno != ENOSYS && errno != EINVAL)
        return false;
    DIR *directory = opendir("/proc/self/fd");
    if (!directory)
        return false;
    int own = dirfd(directory);
    int failure = 0;
    struct dirent *entry;
    for (;;) {
        errno = 0;
        entry = readdir(directory);
        if (!entry) {
            failure = errno;
            break;
        }
        char *end;
        long fd = strtol(entry->d_name, &end, 10);
        if (*entry->d_name && !*end && fd >= first && fd != own)
            close((int)fd);
    }
    closedir(directory);
    errno = failure;
    return !failure;
}

struct start_error {
    uint32_t magic;
    int32_t stage;
    int32_t error;
};

static void child_error(int fd, enum startup_stage stage) {
    struct start_error value = {.magic = START_ERROR_MAGIC, .stage = stage, .error = errno};
    ssize_t ignored = write(fd, &value, sizeof(value));
    (void)ignored;
    _exit(125);
}

static void child(const char *script, int output, int error, int report) {
    int input = open("/dev/null", O_RDWR | O_CLOEXEC);
    if (input < 0)
        child_error(report, START_INPUT);
    if (dup2(input, 0) < 0 || dup2(output, 1) < 0 || dup2(error, 2) < 0)
        child_error(report, START_STDIO);
    if (dup3(report, 3, O_CLOEXEC) < 0)
        child_error(report, START_REPORT);
    if (!close_from(4))
        child_error(3, START_CLOSE);
    if (signal(SIGPIPE, SIG_DFL) == SIG_ERR)
        child_error(3, START_SIGNAL);
    if (setsid() < 0)
        child_error(3, START_SETSID);
    execl("/system/bin/sh", "sh", "-c", script, (char *)NULL);
    child_error(3, START_EXEC);
}

struct capture {
    int fd;
    unsigned char bytes[OUTPUT_CAP];
    size_t saved;
    size_t sent;
    uint64_t read;
    bool cut;
    bool frozen;
};

static void drain(struct capture *stream) {
    if (stream->fd < 0)
        return;
    unsigned char block[READ_SLICE];
    ssize_t length = read(stream->fd, block, sizeof(block));
    if (length > 0) {
        if (!stream->frozen) {
            size_t keep = (size_t)length;
            if (keep > OUTPUT_CAP - stream->saved)
                keep = OUTPUT_CAP - stream->saved;
            memcpy(stream->bytes + stream->saved, block, keep);
            stream->saved += keep;
            stream->read += (uint64_t)length;
        }
    } else if (!length) {
        close_fd(&stream->fd);
    } else if (errno != EAGAIN && errno != EINTR) {
        stream->cut = true;
        close_fd(&stream->fd);
    }
}

struct startup {
    int fd;
    struct start_error report;
    size_t received;
    bool ended;
    bool invalid;
};

static void read_startup(struct startup *value) {
    if (value->fd < 0)
        return;
    unsigned char block[sizeof(struct start_error) + 1];
    ssize_t length = read(value->fd, block, sizeof(block));
    if (length > 0) {
        if ((size_t)length > sizeof(value->report) - value->received) {
            value->invalid = value->ended = true;
            close_fd(&value->fd);
        } else {
            memcpy((unsigned char *)&value->report + value->received, block, (size_t)length);
            value->received += (size_t)length;
        }
    } else if (!length || (errno != EAGAIN && errno != EINTR)) {
        value->invalid =
            length < 0 || (value->received && value->received != sizeof(value->report));
        if (value->received == sizeof(value->report) &&
            (value->report.magic != START_ERROR_MAGIC || value->report.stage < START_INPUT ||
             value->report.stage > START_SETSID || value->report.error <= 0))
            value->invalid = true;
        value->ended = true;
        close_fd(&value->fd);
    }
}

struct child_info {
    pid_t pid;
    bool term;
    bool killed;
};
struct supervisor {
    bool launch_closed;
    bool root_created;
    bool root_reaped;
    bool domain_empty;
    bool foreground_sent;
    bool summary_sent;
    bool output_end_sent;
    bool control_eof;
    bool cleanup_unknown;
    int root_status;
    int input;
    int writes[2];
    int error_write;
    int slot_fd;
    int slot;
    int signal_ok;
    int signal_failed;
    int64_t cancel_at;
    int64_t kill_at;
    int64_t empty_at;
    const char *reason;
    const char *script;
    const char *slot_path;
    unsigned long long slot_device;
    unsigned long long slot_inode;
    pid_t root;
    struct startup startup;
    struct child_info *children;
    size_t count;
    size_t capacity;
};

static bool slot_valid(struct supervisor *value) {
    struct stat held;
    struct stat named;
    if (fstat(value->slot_fd, &held) || lstat(value->slot_path, &named))
        return false;
    if (!same_lock_file(&held, &named) || (unsigned long long)held.st_dev != value->slot_device ||
        (unsigned long long)held.st_ino != value->slot_inode) {
        errno = ESTALE;
        return false;
    }
    return true;
}

static bool take_slot(struct supervisor *value) {
    value->slot_fd = open(value->slot_path, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (value->slot_fd < 0)
        return false;
    for (int i = 0; i < HELPER_SLOTS; ++i) {
        struct flock lock = {.l_type = F_WRLCK, .l_whence = SEEK_SET, .l_start = i, .l_len = 1};
        if (!fcntl(value->slot_fd, F_SETLK, &lock)) {
            value->slot = i;
            return slot_valid(value);
        }
        if (errno != EACCES && errno != EAGAIN)
            return false;
    }
    errno = EBUSY;
    return false;
}

static int parent_of(pid_t pid) {
    char path[64], data[4096], status;
    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    FILE *file = fopen(path, "re");
    if (!file)
        return errno == ENOENT ? -1 : -2;
    int parent = -2;
    if (fgets(data, sizeof(data), file)) {
        char *end = strrchr(data, ')');
        if (end)
            sscanf(end + 2, "%c %d", &status, &parent);
    }
    fclose(file);
    return parent;
}

static void signal_problem(pid_t pid, int sig, int error) {
    char path[64], line[512], encoded[513] = {0};
    long uid = -1;
    snprintf(path, sizeof(path), "/proc/%d/status", pid);
    FILE *file = fopen(path, "re");
    if (file) {
        while (fgets(line, sizeof(line), file))
            if (sscanf(line, "Uid:\t%ld", &uid) == 1)
                break;
        fclose(file);
    }
    snprintf(path, sizeof(path), "/proc/%d/cmdline", pid);
    file = fopen(path, "re");
    if (file) {
        unsigned char bytes[256];
        size_t size = fread(bytes, 1, sizeof(bytes), file);
        for (size_t i = 0; i < size; ++i)
            snprintf(encoded + i * 2, 3, "%02x", bytes[i]);
        fclose(file);
    }
    if (diagnostic_count == DIAGNOSTIC_CAP) {
        diagnostic_dropped++;
        return;
    }
    size_t index = (diagnostic_head + diagnostic_count++) % DIAGNOSTIC_CAP;
    snprintf(diagnostics[index], sizeof(diagnostics[index]),
             "{\"event\":\"signalError\",\"pid\":%d,\"uid\":%ld,\"signal\":%d,\"errno\":%d,"
             "\"commandHex\":\"%s\"}",
             pid, uid, sig, error, encoded);
}

static void send_diagnostic(void) {
    if (diagnostic_count &&
        frame('S', diagnostics[diagnostic_head], strlen(diagnostics[diagnostic_head]))) {
        diagnostic_count--;
        diagnostic_head = (diagnostic_head + 1) % DIAGNOSTIC_CAP;
    }
}

static bool discover(struct supervisor *value) {
    DIR *directory = opendir("/proc");
    if (!directory)
        return false;
    struct dirent *entry;
    bool okay = true;
    for (;;) {
        errno = 0;
        entry = readdir(directory);
        if (!entry) {
            okay = errno == 0;
            break;
        }
        char *end;
        long pid = strtol(entry->d_name, &end, 10);
        if (!*entry->d_name || *end || pid <= 0 || parent_of((pid_t)pid) != getpid())
            continue;
        size_t i;
        for (i = 0; i < value->count; ++i)
            if (value->children[i].pid == pid)
                break;
        if (i != value->count)
            continue;
        if (value->count == value->capacity) {
            size_t size = value->capacity ? value->capacity * 2 : 16;
            void *memory = realloc(value->children, size * sizeof(*value->children));
            if (!memory) {
                okay = false;
                break;
            }
            value->capacity = size;
            value->children = memory;
        }
        value->children[value->count++] = (struct child_info){.pid = (pid_t)pid};
    }
    closedir(directory);
    return okay;
}

static void signal_children(struct supervisor *value) {
    int sig = millis() >= value->kill_at ? SIGKILL : SIGTERM;
    for (size_t i = 0; i < value->count; ++i) {
        struct child_info *entry = &value->children[i];
        bool *sent = sig == SIGTERM ? &entry->term : &entry->killed;
        if (*sent)
            continue;
        int parent =
            entry->pid == value->root && !value->root_reaped ? getpid() : parent_of(entry->pid);
        if (parent == -2) {
            value->cleanup_unknown = true;
            signal_problem(entry->pid, sig, EIO);
            *sent = true;
        }
        if (parent != getpid())
            continue;
        // The sole waiter never reaps between checking the child and signalling it.
        int result = kill(entry->pid, sig);
        int error = result ? errno : 0;
        *sent = true;
        if (!result || error == ESRCH)
            value->signal_ok++;
        else {
            value->signal_failed++;
            signal_problem(entry->pid, sig, error);
        }
    }
}

static void close_launch(struct supervisor *value) {
    close_fd(&value->writes[0]);
    close_fd(&value->writes[1]);
    close_fd(&value->error_write);
}

static void cancel(struct supervisor *value, const char *reason, bool immediate) {
    if (!value->cancel_at) {
        value->cancel_at = millis();
        value->kill_at = value->cancel_at + (immediate ? 0 : TERM_MS);
        value->reason = reason;
    }
    if (immediate && value->kill_at > millis())
        value->kill_at = millis();
    if (!value->launch_closed) {
        value->launch_closed = true;
        close_launch(value);
    }
}

static void launch(struct supervisor *value) {
    if (value->launch_closed)
        return;
    if (!slot_valid(value)) {
        value->cleanup_unknown = true;
        cancel(value, "slotChanged", true);
        return;
    }
    if (!value->capacity) {
        value->children = calloc(16, sizeof(*value->children));
        if (!value->children) {
            value->cleanup_unknown = true;
            cancel(value, "memory", true);
            return;
        }
        value->capacity = 16;
    }
    value->launch_closed = true;
    value->root = fork();
    if (!value->root)
        child(value->script, value->writes[0], value->writes[1], value->error_write);
    if (value->root < 0)
        value->reason = "forkFailed";
    else {
        value->root_created = true;
        value->children[value->count++] = (struct child_info){.pid = value->root};
    }
    close_launch(value);
}

static void reap(struct supervisor *value) {
    if (!value->launch_closed || value->domain_empty)
        return;
    for (int i = 0; i < WAIT_SLICE; ++i) {
        int status;
        pid_t pid = waitpid(-1, &status, __WALL | WNOHANG);
        if (pid > 0) {
            if (pid == value->root) {
                value->root_reaped = true;
                value->root_status = status;
            }
            for (size_t j = 0; j < value->count; ++j)
                if (value->children[j].pid == pid) {
                    value->children[j] = value->children[--value->count];
                    break;
                }
        } else if (!pid)
            return;
        else if (errno == ECHILD) {
            value->domain_empty = true;
            value->empty_at = millis();
            return;
        } else if (errno != EINTR) {
            value->cleanup_unknown = true;
            cancel(value, "wait failure", true);
            return;
        }
    }
}

static bool prepare(void) {
    if (!nonblock(1) || signal(SIGPIPE, SIG_IGN) == SIG_ERR)
        return false;
    if (getuid() != 2000 || geteuid() != 2000 || fpathconf(1, _PC_PIPE_BUF) < FRAME_CAP) {
        errno = EPERM;
        return false;
    }
    struct sigaction action = {.sa_handler = SIG_DFL};
    sigemptyset(&action.sa_mask);
    sigset_t mask;
    sigemptyset(&mask);
    int subreaper = -1;
    if (sigaction(SIGCHLD, &action, NULL) || sigprocmask(SIG_SETMASK, &mask, NULL) ||
        prctl(PR_SET_CHILD_SUBREAPER, 1) || prctl(PR_GET_CHILD_SUBREAPER, &subreaper) ||
        sigaction(SIGCHLD, NULL, &action))
        return false;
    return subreaper == 1 && action.sa_handler == SIG_DFL && !(action.sa_flags & SA_NOCLDWAIT);
}

static void foreground(struct supervisor *value) {
    if (value->foreground_sent)
        return;
    struct startup *startup = &value->startup;
    bool startup_error = !startup->invalid && startup->received == sizeof(startup->report);
    int exit_code =
        value->root_reaped && WIFEXITED(value->root_status) ? WEXITSTATUS(value->root_status) : -1;
    int signal_number =
        value->root_reaped && WIFSIGNALED(value->root_status) ? WTERMSIG(value->root_status) : 0;
    bool startup_unknown = startup->invalid || !startup->ended;
    int startup_stage = startup_error ? startup->report.stage : 0;
    int startup_errno = startup_error ? startup->report.error : 0;
    const char *reason = value->reason ?: "finished";
    value->foreground_sent = state(
        "{\"event\":\"foreground\","
        "\"created\":%s,"
        "\"cancelled\":%s,"
        "\"reason\":\"%s\","
        "\"domainEmpty\":%s,"
        "\"rootDone\":%s,"
        "\"exitCode\":%d,"
        "\"signal\":%d,"
        "\"signalOK\":%d,"
        "\"signalFailed\":%d,"
        "\"cleanupUnknown\":%s,"
        "\"diagnosticDropped\":%zu,"
        "\"startupUnknown\":%s,"
        "\"startupStage\":%d,"
        "\"startupErrno\":%d}",
        value->root_created ? "true" : "false",
        value->cancel_at ? "true" : "false",
        reason,
        value->domain_empty ? "true" : "false",
        value->root_reaped ? "true" : "false",
        exit_code,
        signal_number,
        value->signal_ok,
        value->signal_failed,
        value->cleanup_unknown ? "true" : "false",
        diagnostic_dropped,
        startup_unknown ? "true" : "false",
        startup_stage,
        startup_errno);
}

static void deliver_step(struct supervisor *value, struct capture *streams) {
    if (value->output_end_sent || observer_gone)
        return;
    if (!value->summary_sent) {
        for (int i = 0; i < 2; ++i)
            streams[i].frozen = true;
        const struct capture *stdout_stream = &streams[0];
        const struct capture *stderr_stream = &streams[1];
        bool stdout_cut = stdout_stream->cut || stdout_stream->fd >= 0;
        bool stderr_cut = stderr_stream->cut || stderr_stream->fd >= 0;
        value->summary_sent =
            state("{\"event\":\"outputSummary\",\"read\":[%llu,%llu],"
                  "\"saved\":[%zu,%zu],\"cut\":[%s,%s]}",
                  (unsigned long long)stdout_stream->read,
                  (unsigned long long)stderr_stream->read,
                  stdout_stream->saved,
                  stderr_stream->saved,
                  stdout_cut ? "true" : "false",
                  stderr_cut ? "true" : "false");
        return;
    }
    for (int i = 0; i < 2; ++i) {
        struct capture *stream = &streams[i];
        size_t count = stream->saved - stream->sent;
        if (count > FRAME_PAYLOAD_CAP)
            count = FRAME_PAYLOAD_CAP;
        if (count && frame(i ? 'E' : 'O', stream->bytes + stream->sent, count))
            stream->sent += count;
        if (count)
            return;
    }
    value->output_end_sent = state("{\"event\":\"outputEnd\"}");
}

static void read_control(struct supervisor *value, char control[CONTROL_CAP], size_t *used,
                         int64_t deadline) {
    char block[32];
    ssize_t length = read(value->input, block, sizeof(block));
    if (!length || (length < 0 && errno != EINTR)) {
        close_fd(&value->input);
        value->control_eof = true;
        cancel(value, "worker disconnected", true);
        return;
    }
    if (length < 0)
        return;

    for (ssize_t i = 0; i < length; ++i) {
        if (block[i] == '\n') {
            control[*used] = 0;
            *used = 0;
            // Consume each complete line now; later lines cannot undo its launch/cancel.
            if (!strcmp(control, "CANCEL")) {
                cancel(value, "cancelled", false);
            } else if (!strcmp(control, "GO")) {
                if (millis() < deadline)
                    launch(value);
                else
                    cancel(value, "timeout", false);
            } else {
                value->cleanup_unknown = true;
                cancel(value, "control failure", true);
            }
        } else if (*used < CONTROL_CAP - 1) {
            control[(*used)++] = block[i];
        } else {
            value->cleanup_unknown = true;
            cancel(value, "control overflow", true);
        }
    }
}

int main(int argc, char **argv) {
    if (argc != ARG_COUNT)
        return 64;
    char *end;
    int64_t deadline = strtoll(argv[ARG_DEADLINE], &end, 10);
    if (*end || deadline <= 0)
        return 64;
    struct supervisor supervisor = {
        .root = -1,
        .input = 0,
        .writes = {-1, -1},
        .error_write = -1,
        .slot_fd = -1,
        .slot = -1,
        .script = argv[ARG_SCRIPT],
        .slot_path = argv[ARG_SLOT_PATH],
        .startup = {.fd = -1},
    };
    supervisor.slot_device = strtoull(argv[ARG_SLOT_DEVICE], &end, 10);
    if (*end)
        return 64;
    supervisor.slot_inode = strtoull(argv[ARG_SLOT_INODE], &end, 10);
    if (*end)
        return 64;

    if (!prepare())
        fatal("prepare");
    if (!take_slot(&supervisor)) {
        state("{\"event\":\"rejected\",\"reason\":\"helper capacity unavailable\",\"errno\":%d}",
              errno);
        return 2;
    }
    struct capture *streams = calloc(2, sizeof(*streams));
    if (!streams)
        fatal("memory");
    for (int i = 0; i < 2; ++i)
        streams[i].fd = -1;
    for (int i = 0; i < 2; ++i) {
        int output_pipe[2];
        if (pipe2(output_pipe, O_CLOEXEC) || !nonblock(output_pipe[0]))
            fatal("outputPipe");
        streams[i].fd = output_pipe[0];
        supervisor.writes[i] = output_pipe[1];
    }
    int startup_pipe[2];
    if (pipe2(startup_pipe, O_CLOEXEC) || !nonblock(startup_pipe[0]))
        fatal("startupPipe");
    supervisor.startup.fd = startup_pipe[0];
    supervisor.error_write = startup_pipe[1];
    if (!state("{\"event\":\"ready\",\"pid\":%d,\"slot\":%d}", getpid(), supervisor.slot))
        return 74;

    char control[CONTROL_CAP];
    size_t control_used = 0;
    unsigned drain_turn = 0;
    int64_t last_child_scan_at = 0;
    for (;;) {
        if (millis() >= deadline)
            cancel(&supervisor, "timeout", false);
        if (observer_gone || supervisor.control_eof)
            cancel(&supervisor, "worker disconnected", true);

        struct pollfd fds[] = {
            {.fd = supervisor.input, .events = POLLIN},
            {.fd = streams[0].fd, .events = POLLIN},
            {.fd = streams[1].fd, .events = POLLIN},
            {.fd = supervisor.startup.fd, .events = POLLIN},
        };
        int delay = EXECUTION_POLL_MS;
        if (!observer_gone &&
            (diagnostic_count || (supervisor.foreground_sent && !supervisor.output_end_sent))) {
            delay = DELIVERY_POLL_MS;
        } else if (supervisor.foreground_sent) {
            delay = CLEANUP_POLL_MS;
        }
        if (poll(fds, 4, delay) < 0 && errno != EINTR) {
            supervisor.cleanup_unknown = true;
            cancel(&supervisor, "poll failure", true);
        }
        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            read_control(&supervisor, control, &control_used, deadline);
        }

        // Keep draining and supervising after foreground/output delivery has begun.
        drain(&streams[drain_turn++ % 2]);
        drain(&streams[drain_turn % 2]);
        read_startup(&supervisor.startup);
        if (supervisor.cancel_at && supervisor.root_created && !supervisor.domain_empty &&
            millis() - last_child_scan_at >= CHILD_SCAN_MS) {
            last_child_scan_at = millis();
            if (!discover(&supervisor))
                supervisor.cleanup_unknown = true;
            signal_children(&supervisor);
        }
        reap(&supervisor);
        send_diagnostic();

        bool pipes_drained = streams[0].fd < 0 && streams[1].fd < 0 && supervisor.startup.ended;
        bool foreground_ready =
            (supervisor.domain_empty &&
             (pipes_drained || millis() - supervisor.empty_at >= PIPE_DRAIN_MS)) ||
            (supervisor.cancel_at && millis() >= supervisor.kill_at + REAP_MS);
        if (foreground_ready)
            foreground(&supervisor);
        if (supervisor.foreground_sent)
            deliver_step(&supervisor, streams);

        // Only reap(ECHILD) establishes domain_empty and starts both drain/delivery clocks.
        if (!supervisor.domain_empty)
            continue;
        bool delivery_expired = millis() - supervisor.empty_at > DELIVERY_MS;
        bool delivery_finished = supervisor.output_end_sent && !diagnostic_count;
        if (!observer_gone && !supervisor.control_eof && !delivery_expired && !delivery_finished)
            continue;

        bool reported =
            state("{\"event\":\"helperEnd\",\"rootDone\":%s,"
                  "\"signalFailed\":%d,\"cleanupUnknown\":%s,\"diagnosticDropped\":%zu}",
                  supervisor.root_reaped ? "true" : "false",
                  supervisor.signal_failed,
                  supervisor.cleanup_unknown ? "true" : "false",
                  diagnostic_dropped + diagnostic_count);
        if (!reported && !observer_gone && !supervisor.control_eof && !delivery_expired)
            continue;
        close_fd(&streams[0].fd);
        close_fd(&streams[1].fd);
        close_fd(&supervisor.startup.fd);
        close_fd(&supervisor.input);
        close_fd(&supervisor.slot_fd);
        free(streams);
        free(supervisor.children);
        return 0;
    }
}
