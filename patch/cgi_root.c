/* q2cgi-root: Wi-Fi transfer's CGIs (docs/internals.md#wi-fi-transfer-root). thttpd runs as
 * www-data (/etc/thttpd.conf user=, set by tools/build.py THTTPD_USER), so a flaw in its own
 * request parsing, which runs before any PIN check, reaches an unprivileged process. The card's
 * mount gives only root write access, so the file manager still has to run as root: each of
 * cgi-bin's .cgi files is this program, set-uid root, and it runs Q2CGI (assets/web/q2cgi) as root
 * and nothing else. What it passes on: from the environment just the six variables q2cgi reads,
 * with a fixed PATH; stdin, stdout and stderr (glibc opens /dev/null or /dev/full in place of one
 * closed, as it does for any set-uid program), every other descriptor closed; init's resource
 * limits, every signal at its default and none blocked, umask 022 and thttpd's working directory.
 * So its caller (www-data, which is thttpd, or root; it refuses any other) can do no more through
 * it than a request to thttpd can. No MIPS sysroot: glibc 2.28's declarations, with MIPS o32
 * constants; the host's for test/fw. */
#ifndef Q2CGI /* test/fw/test_cgi_root.py gives its own script and server user */
#define Q2CGI "/usr/bin/q2cgi"
#define WWW_UID 33 /* www-data in stock's /etc/passwd, checked by tools/build.py */
#endif
#ifndef CGI_DIR
#define CGI_DIR "/usr/share/web/cgi-bin" /* where thttpd starts a CGI */
#endif
#define PATH "PATH=/bin:/usr/bin:/sbin:/usr/sbin"
#define NLIMITS 16 /* RLIM_NLIMITS, on MIPS and x86 alike */
#define MAX_FD 65536
#ifdef __mips__
#define NSIG_ 128
#define SIG_SETMASK_ 3
#define RLIMIT_NOFILE_ 5
#else
#define NSIG_ 65
#define SIG_SETMASK_ 2
#define RLIMIT_NOFILE_ 7
#endif

typedef unsigned uid_t_;
struct rlimit_ {
    unsigned long cur, max;
};
int setgroups(unsigned long, const uid_t_ *), setgid(uid_t_), setuid(uid_t_), chdir(const char *);
uid_t_ getuid(void), geteuid(void), getgid(void), getegid(void);
int prlimit(int, int, const struct rlimit_ *, struct rlimit_ *), setrlimit(int, const struct rlimit_ *);
int close(int), sigprocmask(int, const void *, void *), execve(const char *, char *const[], char *const[]);
unsigned umask(unsigned);
long write(int, const void *, unsigned long);
void *signal(int, void *);
int strncmp(const char *, const char *, unsigned long);

/* What q2cgi reads (its header lists them); a name given twice keeps its first value, as getenv does. */
static const char *const KEEP[] = { "REQUEST_METHOD=", "SCRIPT_NAME=",  "QUERY_STRING=",
                                    "CONTENT_LENGTH=", "CONTENT_TYPE=", "HTTP_COOKIE=" };
#define NKEEP (sizeof KEEP / sizeof *KEEP)

static int refuse(void) {
    static const char r[] = "Status: 500 Internal Error\r\nContent-type: application/json\r\n\r\n{}";
    write(1, r, sizeof r - 1);
    return 1;
}

int main(int argc, char **argv, char **envp) {
    char *env[NKEEP + 2] = { PATH };
    unsigned n = 1, sigs[32] = { 0 }; /* a glibc sigset_t, empty */
    struct rlimit_ lim;
    uid_t_ caller = getuid();
    (void)argc, (void)argv;
    if (caller != WWW_UID && caller != 0) return refuse();
    /* all of root's ids, real and saved too, so the caller can't signal or trace what runs */
    if (setgroups(0, 0) || setgid(0) || setuid(0) || getuid() || geteuid() || getgid() || getegid())
        return refuse();
    for (int r = 0; r < NLIMITS; r++)
        if (prlimit(1, r, 0, &lim) || setrlimit(r, &lim)) return refuse();
    if (prlimit(1, RLIMIT_NOFILE_, 0, &lim)) return refuse();
    for (unsigned long fd = 3; fd < lim.max && fd < MAX_FD; fd++) close((int)fd);
    for (int s = 1; s < NSIG_; s++) signal(s, 0); /* SIG_DFL; SIGKILL and SIGSTOP refuse, as they should */
    if (sigprocmask(SIG_SETMASK_, sigs, 0)) return refuse();
    umask(022);
    if (chdir(CGI_DIR)) return refuse();
    for (unsigned k = 0; k < NKEEP; k++)
        for (char **e = envp; *e; e++)
            if (!strncmp(*e, KEEP[k], __builtin_strlen(KEEP[k]))) {
                env[n++] = *e;
                break;
            }
    env[n] = 0;
    char *const args[] = { "q2cgi", 0 };
    execve(Q2CGI, args, env);
    return refuse();
}
