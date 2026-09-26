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
