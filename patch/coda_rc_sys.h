/* What coda-rc uses from libc and SQLite. There is no MIPS sysroot (docs/building.md), so for the
 * Q2 these are declared here with MIPS o32 Linux's constants, as patch/remote.c and patch/video.c declare
 * theirs; CODA_HOST takes the host's own headers instead, for the tests. Only what coda_rc*.c calls. */
#ifndef CODA_RC_SYS_H
#define CODA_RC_SYS_H
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#ifdef CODA_HOST
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/inotify.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#else
typedef long ssize_t;
typedef int pid_t;
typedef unsigned socklen_t;
int __xstat(int, const char *, void *); /* glibc 2.28's stat, version 3 (coda_rc_lib.c lib_key_now) */
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_APPEND 0x8
#define O_NONBLOCK 0x80
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_CLOEXEC 0x80000
#define F_SETFD 2
#define FD_CLOEXEC 1
#define F_GETFL 3
#define F_SETFL 4
#define AF_INET 2
#define SOCK_STREAM 2 /* MIPS swaps STREAM and DGRAM */
#define SOCK_DGRAM 1
#define SOCK_RAW 3
#define SOCK_SEQPACKET 5
#define IPPROTO_IP 0
#define IP_MULTICAST_IF 32
#define IP_MULTICAST_TTL 33
#define IP_ADD_MEMBERSHIP 35
#define SIOCGIFADDR 0x8915
#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 4
#define SO_SNDBUF 0x1001
#define SO_SNDTIMEO 0x1005
#define IPPROTO_TCP 6
#define TCP_NODELAY 1
#define MSG_NOSIGNAL 0x4000
#define POLLIN 1
#define POLLOUT 4
#define POLLERR 8
#define POLLHUP 0x10
#define EINTR 4
#define EAGAIN 11
#define SIGHUP 1
#define SIGPIPE 13
#define SIGTERM 15
#define SIGUSR1 16 /* MIPS's */
#define SIG_IGN ((void (*)(int))1)
#define CLOCK_MONOTONIC 1
#define PRIO_PROCESS 0
#define INADDR_ANY 0u
#define errno (*__errno_location())
struct in_addr { uint32_t s_addr; };
struct sockaddr_in { unsigned short sin_family, sin_port; struct in_addr sin_addr; char zero[8]; };
struct ip_mreq { struct in_addr imr_multiaddr, imr_interface; };
struct pollfd { int fd; short events, revents; };
struct timespec { long tv_sec, tv_nsec; };
struct timeval { long tv_sec, tv_usec; };
int *__errno_location(void);
int open(const char *, int, ...), close(int), unlink(const char *), rename(const char *, const char *);
int mkdir(const char *, unsigned), chmod(const char *, unsigned), fsync(int), access(const char *, int);
ssize_t read(int, void *, size_t), write(int, const void *, size_t);
int fcntl(int, int, ...), ftruncate(int, long);
long lseek(int, long, int);
int socket(int, int, int), setsockopt(int, int, int, const void *, socklen_t), bind(int, const void *, socklen_t);
int listen(int, int), accept(int, void *, socklen_t *), connect(int, const void *, socklen_t);
int getsockname(int, void *, socklen_t *);
int getpeername(int, void *, socklen_t *);
ssize_t send(int, const void *, size_t, int);
ssize_t sendto(int, const void *, size_t, int, const void *, socklen_t);
ssize_t recvfrom(int, void *, size_t, int, void *, socklen_t *);
int ioctl(int, unsigned long, ...);
int poll(struct pollfd *, unsigned long, int);
#define IN_MOVED_TO 0x80
#define IN_CLOSE_WRITE 0x8
#define IN_DELETE 0x200
#define IN_NONBLOCK O_NONBLOCK
#define IN_CLOEXEC O_CLOEXEC
int inotify_init1(int), inotify_add_watch(int, const char *, uint32_t);
int clock_gettime(int, struct timespec *);
long time(long *);
int getpid(void), kill(int, int), setpriority(int, unsigned, int);
void (*signal(int, void (*)(int)))(int);
void *malloc(size_t), *calloc(size_t, size_t), *realloc(void *, size_t), free(void *);
void *memcpy(void *, const void *, size_t), *memset(void *, int, size_t), *memmove(void *, const void *, size_t);
int memcmp(const void *, const void *, size_t), strcmp(const char *, const char *);
int strncmp(const char *, const char *, size_t);
size_t strlen(const char *);
char *strchr(const char *, int), *strrchr(const char *, int), *strstr(const char *, const char *);
void *memchr(const void *, int, size_t);
int snprintf(char *, size_t, const char *, ...), vsnprintf(char *, size_t, const char *, va_list);
int atoi(const char *);
unsigned long strtoul(const char *, char **, int);
void exit(int);
uint16_t htons(uint16_t);
uint32_t htonl(uint32_t);
/* glibc 2.28's glob_t on 32-bit MIPS: the count, the paths, then seven words glob keeps for itself */
typedef struct { size_t gl_pathc; char **gl_pathv; size_t gl_offs; int gl_flags; void *gl_private[5]; } glob_t;
int glob(const char *, int, int (*)(const char *, int), glob_t *);
void globfree(glob_t *);
#endif

/* ---- SQLite (the rootfs's libsqlite3.so.0, 3.54; docs/third-party.md) ---- */
typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef struct sqlite3_context sqlite3_context;
typedef struct sqlite3_value sqlite3_value;
#define SQLITE_OK 0
#define SQLITE_ROW 100
#define SQLITE_DONE 101
#define SQLITE_OPEN_READONLY 1
#define SQLITE_UTF8 1
#define SQLITE_DETERMINISTIC 0x800
#define SQLITE_TRANSIENT ((void (*)(void *)) - 1)
#define SQLITE_STATIC ((void (*)(void *))0)
int sqlite3_open_v2(const char *, sqlite3 **, int, const char *);
int sqlite3_close(sqlite3 *);
int sqlite3_busy_timeout(sqlite3 *, int);
int sqlite3_exec(sqlite3 *, const char *, int (*)(void *, int, char **, char **), void *, char **);
int sqlite3_prepare_v2(sqlite3 *, const char *, int, sqlite3_stmt **, const char **);
int sqlite3_step(sqlite3_stmt *);
int sqlite3_finalize(sqlite3_stmt *), sqlite3_reset(sqlite3_stmt *);
int sqlite3_bind_text(sqlite3_stmt *, int, const char *, int, void (*)(void *));
int sqlite3_bind_int(sqlite3_stmt *, int, int);
const unsigned char *sqlite3_column_text(sqlite3_stmt *, int);
int sqlite3_column_int(sqlite3_stmt *, int);
const char *sqlite3_errmsg(sqlite3 *);
int sqlite3_create_function(sqlite3 *, const char *, int, int, void *, void (*)(sqlite3_context *, int, sqlite3_value **),
                            void (*)(sqlite3_context *, int, sqlite3_value **), void (*)(sqlite3_context *));
const unsigned char *sqlite3_value_text(sqlite3_value *);
int sqlite3_value_int(sqlite3_value *);
void sqlite3_result_text(sqlite3_context *, const char *, int, void (*)(void *));
void sqlite3_result_int(sqlite3_context *, int);
void sqlite3_result_null(sqlite3_context *);
#endif
