#define _GNU_SOURCE
#include "helper_slots.h"
#include <errno.h>
#include <fcntl.h>
#include <jni.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static void error(JNIEnv *env, const char *operation) {
    int saved = errno;
    char message[256];
    snprintf(message, sizeof(message), "%s: %s (errno=%d)", operation, strerror(saved), saved);
    jclass type = (*env)->FindClass(env, "java/io/IOException");
    if (type) (*env)->ThrowNew(env, type, message);
}

static char *path(JNIEnv *env, jbyteArray bytes) {
    jsize length = (*env)->GetArrayLength(env, bytes);
    char *value = malloc((size_t)length + 1);
    if (!value) { errno = ENOMEM; error(env, "path"); return NULL; }
    (*env)->GetByteArrayRegion(env, bytes, 0, length, (jbyte *)value);
    if ((*env)->ExceptionCheck(env)) { free(value); return NULL; }
    value[length] = 0;
    return value;
}

JNIEXPORT jint JNICALL Java_com_rainng_androidctl_NativeFiles_openDirectory(JNIEnv *env, jobject self, jbyteArray bytes) {
    (void)self;
    char *value = path(env, bytes);
    if (!value) return -1;
    int fd = open(value, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int saved = errno;
    free(value);
    if (fd < 0) {
        errno = saved;
        error(env, "open directory");
    }
    return fd;
}

JNIEXPORT jint JNICALL Java_com_rainng_androidctl_NativeFiles_openFile(JNIEnv *env, jobject self, jint directory, jbyteArray bytes, jboolean create) {
    (void)self;
    char *value = path(env, bytes);
    if (!value) return -1;
    int flags = O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK | (create ? O_RDWR | O_CREAT | O_EXCL : O_RDONLY);
    int fd = openat(directory, value, flags, 0600);
    int saved = errno;
    free(value);
    if (fd < 0) {
        errno = saved;
        error(env, "open file");
        return -1;
    }
    if (create) return fd;
    struct stat info;
    if (fstat(fd, &info)) {
        saved = errno;
    } else {
        saved = S_ISREG(info.st_mode) ? 0 : EINVAL;
    }
    if (saved) {
        close(fd);
        errno = saved;
        error(env, "regular file required");
        return -1;
    }
    return fd;
}

static int ordinary_target_exists(int directory, const char *value) {
    struct stat info;
    if (fstatat(directory, value, &info, AT_SYMLINK_NOFOLLOW)) {
        return errno == ENOENT ? 0 : -1;
    }
    if (!S_ISREG(info.st_mode)) {
        errno = EINVAL;
        return -1;
    }
    return 1;
}

JNIEXPORT jboolean JNICALL Java_com_rainng_androidctl_NativeFiles_targetExists(JNIEnv *env, jobject self, jint directory, jbyteArray bytes) {
    (void)self;
    char *value = path(env, bytes);
    if (!value) return JNI_FALSE;
    int result = ordinary_target_exists(directory, value);
    int saved = errno;
    free(value);
    if (result < 0) {
        errno = saved;
        error(env, "regular target required");
    }
    return result == 1 ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jint JNICALL Java_com_rainng_androidctl_NativeFiles_publish(JNIEnv *env, jobject self, jint directory, jbyteArray from, jbyteArray to, jboolean replace) {
    (void)self;
    /* Path failures propagate a pending JNI exception, so the zero returns below are not decoded. */
    char *source = path(env, from);
    if (!source) return 0;
    char *destination = path(env, to);
    if (!destination) {
        free(source);
        return 0;
    }

    int publication_attempted = 0;
    int saved_errno = 0;
    int rename_result;
    int target_status = ordinary_target_exists(directory, destination);
    if (target_status < 0) {
        saved_errno = errno;
        goto release_paths;
    }
    if (target_status == 1 && !replace) {
        saved_errno = EEXIST;
        goto release_paths;
    }

    publication_attempted = 1;
    rename_result = (int)syscall(SYS_renameat2, directory, source, directory, destination, replace ? 0 : RENAME_NOREPLACE);
    if (rename_result == 0) goto release_paths;
    saved_errno = errno;
    if (replace || saved_errno != EINVAL) goto release_paths;

    /* Some Android FUSE mounts reject NOREPLACE. Recheck ordinary conflicts once. */
    publication_attempted = 0;
    target_status = ordinary_target_exists(directory, destination);
    if (target_status < 0) {
        saved_errno = errno;
        goto release_paths;
    }
    if (target_status == 1) {
        saved_errno = EEXIST;
        goto release_paths;
    }

    publication_attempted = 1;
    rename_result = renameat(directory, source, directory, destination);
    saved_errno = rename_result < 0 ? errno : 0;

release_paths:
    free(source);
    free(destination);
    return publication_attempted ? saved_errno : -saved_errno;
}

JNIEXPORT jboolean JNICALL Java_com_rainng_androidctl_NativeFiles_removeOwned(JNIEnv *env, jobject self, jint directory, jbyteArray bytes, jlong device, jlong inode) {
    (void)self;
    char *value = path(env, bytes);
    if (!value) return JNI_FALSE;
    struct stat info;
    int result = fstatat(directory, value, &info, AT_SYMLINK_NOFOLLOW);
    if (result < 0 && errno == ENOENT) { free(value); return JNI_TRUE; }
    if (result == 0 && (!S_ISREG(info.st_mode) || (jlong)info.st_dev != device || (jlong)info.st_ino != inode)) {
        free(value);
        return JNI_FALSE;
    }
    if (result == 0) result = unlinkat(directory, value, 0);
    int saved = errno;
    free(value);
    if (result < 0) { errno = saved; error(env, "remove owned temporary file"); return JNI_FALSE; }
    return JNI_TRUE;
}

JNIEXPORT void JNICALL Java_com_rainng_androidctl_NativeFiles_close(JNIEnv *env, jobject self, jint fd) {
    (void)self;
    if (close(fd)) error(env, "close file descriptor");
}

JNIEXPORT jint JNICALL Java_com_rainng_androidctl_NativeFiles_openLockFile(JNIEnv *env, jobject self, jbyteArray bytes) {
    (void)self;
    char *value = path(env, bytes);
    if (!value) return -1;
    int fd = open(value, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    int saved = errno;
    free(value);
    if (fd < 0) { errno = saved; error(env, "open lock file"); }
    return fd;
}

JNIEXPORT jlongArray JNICALL Java_com_rainng_androidctl_NativeFiles_checkLockFile(JNIEnv *env, jobject self, jint fd, jbyteArray bytes) {
    (void)self;
    char *value = path(env, bytes);
    if (!value) return NULL;
    struct stat held, named;
    int result = fstat(fd, &held);
    if (!result) result = lstat(value, &named);
    int saved = errno;
    free(value);
    if (result) { errno = saved; error(env, "lock file identity"); return NULL; }
    if (!same_lock_file(&held, &named)) {
        errno = ESTALE; error(env, "lock file removed or replaced"); return NULL;
    }
    jlong values[] = {(jlong)held.st_dev, (jlong)held.st_ino};
    jlongArray output = (*env)->NewLongArray(env, 2);
    if (output) (*env)->SetLongArrayRegion(env, output, 0, 2, values);
    return output;
}

JNIEXPORT jboolean JNICALL Java_com_rainng_androidctl_NativeFiles_tryExclusiveLock(JNIEnv *env, jobject self, jint fd) {
    (void)self;
    if (!flock(fd, LOCK_EX | LOCK_NB)) return JNI_TRUE;
    if (errno != EWOULDBLOCK) error(env, "worker lock");
    return JNI_FALSE;
}

JNIEXPORT jintArray JNICALL Java_com_rainng_androidctl_NativeFiles_slotOwners(JNIEnv *env, jobject self, jint fd) {
    (void)self;
    jint values[HELPER_SLOTS];
    for (int i = 0; i < HELPER_SLOTS; ++i) {
        struct flock lock = {.l_type = F_WRLCK, .l_whence = SEEK_SET, .l_start = i, .l_len = 1};
        if (fcntl(fd, F_GETLK, &lock)) { error(env, "inspect helper slots"); return NULL; }
        values[i] = lock.l_type == F_UNLCK ? 0 : lock.l_pid;
    }
    jintArray output = (*env)->NewIntArray(env, HELPER_SLOTS);
    if (output) (*env)->SetIntArrayRegion(env, output, 0, HELPER_SLOTS, values);
    return output;
}
