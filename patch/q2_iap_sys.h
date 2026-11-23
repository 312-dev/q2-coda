/* What q2-iap (patch/q2_iap.c and the files built with it, tools/build.py Q2IAP_SOURCES) uses from glibc 2.28. There
 * is no MIPS sysroot (docs/building.md), so for the Q2 these are declared here with MIPS o32 Linux's constants, as
 * coda_rc_sys.h declares coda-rc's; PROBE_HOST takes the host's own headers instead (test/iap_probe.py). */
#ifndef Q2_IAP_SYS_H
#define Q2_IAP_SYS_H
#ifdef PROBE_HOST
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/klog.h>
#include <sys/wait.h>
#define ERRNO errno
#else
typedef unsigned int size_t;
typedef int ssize_t;
struct timespec {
    long tv_sec, tv_nsec;
};
struct pollfd {
    int fd;
    short events, revents;
};
int open(const char *, int, ...);
int close(int);
ssize_t read(int, void *, size_t);
ssize_t write(int, const void *, size_t);
int poll(struct pollfd *, unsigned long, int);
int clock_gettime(int, struct timespec *);
long time(long *);
long atol(const char *);
int snprintf(char *, size_t, const char *, ...);
int strcmp(const char *, const char *);
int strncmp(const char *, const char *, size_t);
size_t strlen(const char *);
char *strchr(const char *, int);
void *memcpy(void *, const void *, size_t);
void *memset(void *, int, size_t);
int memcmp(const void *, const void *, size_t);
int atoi(const char *);
int fork(void);
int nanosleep(const struct timespec *, struct timespec *);
int siginterrupt(int, int);
void _exit(int);
unsigned alarm(unsigned);
void (*signal(int, void (*)(int)))(int);
int fsync(int);
void *memmove(void *, const void *, size_t);
int pipe(int *);
int fcntl(int, int, ...);
int kill(int, int);
int rename(const char *, const char *);
int unlink(const char *);
char *strrchr(const char *, int);
char *strstr(const char *, const char *);
void *memchr(const void *, int, size_t);
int *__errno_location(void);
void *malloc(size_t);
void *realloc(void *, size_t);
void free(void *);
unsigned long strtoul(const char *, char **, int);
int access(const char *, int);
int klogctl(int, char *, int);
int execl(const char *, const char *, ...);
void qsort(void *, size_t, size_t, int (*)(const void *, const void *));
int nice(int);
int waitpid(int, int *, int);
#define WNOHANG 1
#define O_CLOEXEC 0x80000 /* MIPS */
#define F_SETFD 2
#define FD_CLOEXEC 1
void *bsearch(const void *, const void *, size_t, size_t, int (*)(const void *, const void *));
#define ERRNO (*__errno_location())
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_APPEND 0x0008 /* MIPS */
#define O_CREAT 0x0100  /* MIPS */
#define O_TRUNC 0x0200  /* MIPS */
#define POLLIN 1
#define CLOCK_MONOTONIC 1
#define CLOCK_BOOTTIME 7
#define F_SETFL 4
#define O_NONBLOCK 0x0080 /* MIPS */
#define SIGHUP 1
#define SIGINT 2
#define SIGPIPE 13
#define SIGALRM 14
#define SIGTERM 15
#define SIGCHLD 18 /* MIPS */
#define SIG_IGN ((void (*)(int))1)
#define SIG_DFL ((void (*)(int))0)
#define EINTR 4
#define EAGAIN 11
#endif
#endif
