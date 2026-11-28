/* q2ask SOCKET HELLO [LINE...]: one exchange with a Vamp API 1 service (docs/platform/README.md#the-common-shape), for
 * a shell on the Q2 (adb) and the first card boot's checks (docs/vamp-os-base.md#first-card-boot). It sends HELLO, then
 * each LINE, and prints every line answered until each LINE has had a line and then 300 ms pass with nothing more (a
 * list's lines and its "ok"), or 2 s pass with nothing at all.
 * Exit 0 when the greeting was "HELLO ok", 1 when it was not, 2 when the socket would not connect.
 *   q2ask /run/vamp/sys.sock Q2SYS/1 battery
 *   q2ask /run/vamp/play.sock Q2PLAY/1 now */
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 3) return fprintf(stderr, "usage: q2ask SOCKET HELLO [LINE...]\n"), 2;
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", argv[1]);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a)) return perror(argv[1]), 2;
    char out[4096];
    int n = snprintf(out, sizeof out, "%s\n", argv[2]);
    for (int i = 3; i < argc && n < (int)sizeof out; ++i) n += snprintf(out + n, sizeof out - (size_t)n, "%s\n", argv[i]);
    if (n >= (int)sizeof out || send(fd, out, (size_t)n, MSG_NOSIGNAL) != n) return perror("send"), 2;
    int want = argc - 2, got = 0, greeted = -1; /* the greeting's line and one a LINE */
    char in[8192], ok[200];
    size_t len = 0;
    snprintf(ok, sizeof ok, "%s ok", argv[2]);
    struct pollfd p = {.fd = fd, .events = POLLIN};
    while (poll(&p, 1, got < want ? 2000 : 300) > 0) {
        ssize_t k = recv(fd, in + len, sizeof in - 1 - len, 0);
        if (k <= 0) break;
        len += (size_t)k, in[len] = 0;
        for (char *nl; (nl = strchr(in, '\n'));) {
            *nl = 0;
            puts(in);
            if (greeted < 0) greeted = !strcmp(in, ok);
            ++got;
            len -= (size_t)(nl + 1 - in);
            memmove(in, nl + 1, len + 1);
        }
        if (len == sizeof in - 1) len = 0;
    }
    close(fd);
    return greeted == 1 ? 0 : 1;
}
