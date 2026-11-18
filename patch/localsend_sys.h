/* What q2send (patch/localsend.c, patch/localsend_core.c) uses from glibc 2.28, libpthread and OpenSSL 1.1.1
 * (the rootfs's libssl.so.1.1 and libcrypto.so.1.1, docs/third-party.md#openssl). There is no MIPS sysroot
 * (docs/building.md), so for the Q2 these are declared here with MIPS o32 Linux's constants, as coda_rc_sys.h
 * declares coda-rc's; LS_HOST takes the host's own headers instead, for test/unit/test_localsend.py. Only what
 * the two files call, and only OpenSSL calls that 1.1.1 and the host's 3.x both have. */
#ifndef LOCALSEND_SYS_H
#define LOCALSEND_SYS_H
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#ifdef LS_HOST
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#define ls_statvfs statvfs
typedef struct statvfs ls_vfs;
#define LS_VFS_FREE(v) ((long long)(v).f_bavail * (long long)(v).f_frsize)
#define ls_lseek lseek
#define O_LS_LARGE 0
#else
typedef long ssize_t;
typedef unsigned socklen_t;
#define O_RDONLY 0
#define O_WRONLY 1
#define O_APPEND 0x8
#define O_NONBLOCK 0x80
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_EXCL 0x400
#define O_LS_LARGE 0x2000 /* O_LARGEFILE */
#define O_CLOEXEC 0x80000
#define F_GETFL 3
#define F_SETFL 4
#define SEEK_END 2
#define AF_INET 2
#define SOCK_STREAM 2 /* MIPS swaps STREAM and DGRAM */
#define SOCK_DGRAM 1
#define SOCK_CLOEXEC 0x80000
#define IPPROTO_IP 0
#define IP_MULTICAST_IF 32
#define IP_MULTICAST_TTL 33
#define IP_MULTICAST_LOOP 34
#define IP_ADD_MEMBERSHIP 35
#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 4
#define SO_REUSEPORT 0x200
#define SO_ERROR 0x1007
#define SO_SNDTIMEO 0x1005
#define SO_RCVTIMEO 0x1006
#define MSG_PEEK 2
#define MSG_DONTWAIT 0x40
#define MSG_NOSIGNAL 0x4000
#define SHUT_RDWR 2
#define POLLIN 1
#define POLLOUT 4
#define POLLERR 8
#define POLLHUP 0x10
#define EINTR 4
#define EAGAIN 11
#define EEXIST 17
#define EINPROGRESS 150 /* MIPS */
#define SIGINT 2
#define SIGPIPE 13
#define SIGTERM 15
#define SIG_IGN ((void (*)(int))1)
#define CLOCK_MONOTONIC 1
#define INADDR_ANY 0u
#define errno (*__errno_location())
struct in_addr {
    uint32_t s_addr;
};
struct sockaddr_in {
    unsigned short sin_family, sin_port;
    struct in_addr sin_addr;
    char zero[8];
};
struct ip_mreq {
    struct in_addr imr_multiaddr, imr_interface;
};
struct pollfd {
    int fd;
    short events, revents;
};
struct timespec {
    long tv_sec, tv_nsec;
};
struct timeval {
    long tv_sec, tv_usec;
};
/* glibc's struct statvfs64 on 32-bit: f_bsize, f_frsize, then 64-bit counts; f_bavail at byte 24 */
typedef struct {
    unsigned long f_bsize, f_frsize;
    unsigned long long f_blocks, f_bfree, f_bavail, f_files, f_ffree, f_favail;
    unsigned long rest[10];
} ls_vfs;
int statvfs64(const char *, ls_vfs *);
#define ls_statvfs statvfs64
#define LS_VFS_FREE(v) ((long long)(v).f_bavail * (long long)(v).f_frsize)
long long lseek64(int, long long, int);
#define ls_lseek lseek64
int *__errno_location(void);
int open(const char *, int, ...), close(int), unlink(const char *), rename(const char *, const char *);
int mkdir(const char *, unsigned), access(const char *, int), fsync(int);
ssize_t read(int, void *, size_t), write(int, const void *, size_t);
int fcntl(int, int, ...);
int socket(int, int, int), setsockopt(int, int, int, const void *, socklen_t), bind(int, const void *, socklen_t);
int getsockopt(int, int, int, void *, socklen_t *);
int listen(int, int), accept(int, void *, socklen_t *), connect(int, const void *, socklen_t), shutdown(int, int);
ssize_t recv(int, void *, size_t, int);
ssize_t sendto(int, const void *, size_t, int, const void *, socklen_t);
ssize_t recvfrom(int, void *, size_t, int, void *, socklen_t *);
int poll(struct pollfd *, unsigned long, int);
int clock_gettime(int, struct timespec *);
int getpid(void);
void (*signal(int, void (*)(int)))(int);
void *malloc(size_t), *calloc(size_t, size_t), *realloc(void *, size_t), free(void *);
void *memcpy(void *, const void *, size_t), *memset(void *, int, size_t), *memmove(void *, const void *, size_t);
int memcmp(const void *, const void *, size_t), strcmp(const char *, const char *);
int strncmp(const char *, const char *, size_t);
size_t strlen(const char *);
char *strchr(const char *, int), *strrchr(const char *, int), *strstr(const char *, const char *);
void *memchr(const void *, int, size_t);
int snprintf(char *, size_t, const char *, ...), vsnprintf(char *, size_t, const char *, va_list);
long long strtoll(const char *, char **, int);
unsigned long long strtoull(const char *, char **, int);
unsigned long strtoul(const char *, char **, int);
void exit(int);
uint16_t htons(uint16_t), ntohs(uint16_t);
/* libpthread: glibc's 32-bit pthread_mutex_t is 24 bytes, all zero when unlocked (PTHREAD_MUTEX_INITIALIZER) */
typedef unsigned long pthread_t;
typedef struct {
    int w[6];
} pthread_mutex_t;
#define PTHREAD_MUTEX_INITIALIZER {{0}}
int pthread_create(pthread_t *, const void *, void *(*)(void *), void *), pthread_detach(pthread_t);
int pthread_mutex_lock(pthread_mutex_t *), pthread_mutex_unlock(pthread_mutex_t *);

/* OpenSSL 1.1.1: opaque types and the calls q2send makes */
typedef struct ssl_st SSL;
typedef struct ssl_ctx_st SSL_CTX;
typedef struct ssl_method_st SSL_METHOD;
typedef struct x509_st X509;
typedef struct X509_name_st X509_NAME;
typedef struct x509_store_ctx_st X509_STORE_CTX;
typedef struct evp_pkey_st EVP_PKEY;
typedef struct evp_md_st EVP_MD;
typedef struct evp_md_ctx_st EVP_MD_CTX;
typedef struct ec_key_st EC_KEY;
typedef struct asn1_string_st ASN1_INTEGER, ASN1_TIME;
#define SSL_VERIFY_NONE 0
#define SSL_VERIFY_PEER 1
#define SSL_ERROR_WANT_READ 2
#define SSL_ERROR_WANT_WRITE 3
#define SSL_CTRL_SET_MIN_PROTO_VERSION 123
#define TLS1_2_VERSION 0x0303
#define SSL_OP_CIPHER_SERVER_PREFERENCE 0x00400000UL
#define SSL_OP_PRIORITIZE_CHACHA 0x00200000UL
#define SSL_OP_NO_RENEGOTIATION 0x40000000UL
#define NID_X9_62_prime256v1 415
#define EVP_PKEY_EC 408
#define MBSTRING_ASC 0x1001
#define SSL_CTX_set_min_proto_version(c, v) SSL_CTX_ctrl(c, SSL_CTRL_SET_MIN_PROTO_VERSION, v, 0)
const SSL_METHOD *TLS_server_method(void), *TLS_client_method(void);
SSL_CTX *SSL_CTX_new(const SSL_METHOD *);
void SSL_CTX_free(SSL_CTX *);
long SSL_CTX_ctrl(SSL_CTX *, int, long, void *);
unsigned long SSL_CTX_set_options(SSL_CTX *, unsigned long);
int SSL_CTX_use_certificate(SSL_CTX *, X509 *), SSL_CTX_use_PrivateKey(SSL_CTX *, EVP_PKEY *);
void SSL_CTX_set_verify(SSL_CTX *, int, int (*)(int, X509_STORE_CTX *));
int SSL_CTX_set_cipher_list(SSL_CTX *, const char *), SSL_CTX_set_ciphersuites(SSL_CTX *, const char *);
SSL *SSL_new(SSL_CTX *);
void SSL_free(SSL *);
int SSL_set_fd(SSL *, int), SSL_accept(SSL *), SSL_connect(SSL *), SSL_shutdown(SSL *);
int SSL_read(SSL *, void *, int), SSL_write(SSL *, const void *, int), SSL_get_error(const SSL *, int);
X509 *SSL_get_peer_certificate(const SSL *);
X509 *X509_new(void);
void X509_free(X509 *);
int X509_set_version(X509 *, long), X509_set_pubkey(X509 *, EVP_PKEY *), X509_set_issuer_name(X509 *, X509_NAME *);
ASN1_INTEGER *X509_get_serialNumber(X509 *);
int ASN1_INTEGER_set(ASN1_INTEGER *, long);
ASN1_TIME *X509_getm_notBefore(const X509 *), *X509_getm_notAfter(const X509 *);
int ASN1_TIME_set_string(ASN1_TIME *, const char *);
X509_NAME *X509_get_subject_name(const X509 *);
int X509_NAME_add_entry_by_txt(X509_NAME *, const char *, int, const unsigned char *, int, int, int);
int X509_sign(X509 *, EVP_PKEY *, const EVP_MD *);
int i2d_X509(X509 *, unsigned char **);
X509 *d2i_X509(X509 **, const unsigned char **, long);
int i2d_PrivateKey(EVP_PKEY *, unsigned char **);
EVP_PKEY *d2i_AutoPrivateKey(EVP_PKEY **, const unsigned char **, long);
EVP_PKEY *EVP_PKEY_new(void);
void EVP_PKEY_free(EVP_PKEY *);
int EVP_PKEY_assign(EVP_PKEY *, int, void *);
EC_KEY *EC_KEY_new_by_curve_name(int);
int EC_KEY_generate_key(EC_KEY *);
void EC_KEY_free(EC_KEY *);
const EVP_MD *EVP_sha256(void);
EVP_MD_CTX *EVP_MD_CTX_new(void);
void EVP_MD_CTX_free(EVP_MD_CTX *);
int EVP_DigestInit_ex(EVP_MD_CTX *, const EVP_MD *, void *), EVP_DigestUpdate(EVP_MD_CTX *, const void *, size_t);
int EVP_DigestFinal_ex(EVP_MD_CTX *, unsigned char *, unsigned *);
int RAND_bytes(unsigned char *, int);
void CRYPTO_free(void *, const char *, int);
#define OPENSSL_free(p) CRYPTO_free(p, "", 0)
#endif
#endif
