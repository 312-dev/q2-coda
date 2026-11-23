/* What q2_iap.c, q2-iap's main file, gives the files built with it (tools/build.py Q2IAP_SOURCES): the log, the
 * clocks, its children's set-up, small file I/O and the shim's commands. */
#ifndef Q2_IAP_H
#define Q2_IAP_H

long mono_ms(void);
long boot_ms(void);
void logline(const char *tag, const char *msg);
int pipe_cloexec(int *fds);
void child_drop(void);
int write_whole(const char *path, const char *b, unsigned n);
void io_command(const char *verb);
unsigned read_file(const char *path, unsigned char *out, unsigned cap);
unsigned fnv_part(unsigned h, const char *s);

#endif
