#ifndef ANDROIDCTL_HELPER_SLOTS_H
#define ANDROIDCTL_HELPER_SLOTS_H

#include <stdbool.h>
#include <sys/stat.h>

#define HELPER_SLOTS 4

static inline bool same_lock_file(const struct stat *held, const struct stat *named) {
    return S_ISREG(held->st_mode) && S_ISREG(named->st_mode) && held->st_nlink == 1 && named->st_nlink == 1 &&
        held->st_dev == named->st_dev && held->st_ino == named->st_ino;
}

#endif
