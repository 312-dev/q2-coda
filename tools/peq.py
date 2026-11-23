"""The payload's C beside navigation.c and its stock imports (LIBC, compile_common); no vendor code is distributed."""
import re
from build import ROOT, FLAGS, FILL, GP, FUNCTIONS, GLOBALS, PRIVATE_FUNCTIONS, check, run, symbols

LIBC = {
    'dlsym': ('void *', 'void *, const char *'),
    'memset': ('void *', 'void *, int, unsigned'),
    'memcpy': ('void *', 'void *, const void *, unsigned'),
    'memcmp': ('int', 'const void *, const void *, unsigned'),
    'strcmp': ('int', 'const char *, const char *'),
    'strlen': ('unsigned', 'const char *'),
    'fputs': ('int', 'const char *, void *'),
    'calloc': ('void *', 'unsigned, unsigned'),
    'realloc': ('void *', 'void *, unsigned'),  # addon.c got: a kept reply to its size
    'free': ('void', 'void *'),
    'memmove': ('void *', 'void *, const void *, unsigned'),
    'pow': ('double', 'double, double'), 'cos': ('double', 'double'),
    'sin': ('double', 'double'), 'log': ('double', 'double'),
    'fopen': ('void *', 'const char *, const char *'),
    'fread': ('unsigned', 'void *, unsigned, unsigned, void *'),
    'fwrite': ('unsigned', 'const void *, unsigned, unsigned, void *'),
    'fclose': ('int', 'void *'), 'ferror': ('int', 'void *'),
    'fflush': ('int', 'void *'), 'fileno': ('int', 'void *'), 'fsync': ('int', 'int'),
    'rename': ('int', 'const char *, const char *'), 'unlink': ('int', 'const char *'),
    '__xstat': ('int', 'int, const char *, void *'),
    'access': ('int', 'const char *, int'), 'mkdir': ('int', 'const char *, unsigned'),
    'rmdir': ('int', 'const char *'),  # Apps' Remove (netupdate.c)
    'lseek64': ('long long', 'int, long long, int'), 'read': ('int', 'int, void *, unsigned'),
    'snprintf': ('int', 'char *, unsigned, const char *, ...'),
    'llrint': ('long long', 'double'),
    '__isoc99_sscanf': ('int', 'const char *, const char *, ...'),  # peq.c peq_number: no strtod in demo's GOT
    'opendir': ('void *', 'const char *'), 'closedir': ('int', 'void *'),
    'readdir': ('struct dirent *', 'void *'),
    'qsort': ('void', 'void *, unsigned, unsigned, int (*)(const void *, const void *)'),
    # libcstl and socket imports for the Play/Pause queue menu in navigation.c
    '_create_deque': ('void *', 'const char *'), 'deque_init': ('void', 'void *'),
    'deque_init_copy': ('void', 'void *, const void *'), 'deque_size': ('unsigned', 'const void *'),
    'deque_at': ('void *', 'const void *, unsigned'), '_deque_push_back': ('void', 'void *, ...'),
    'deque_assign': ('void', 'void *, const void *'), 'deque_clear': ('void', 'void *'),
    'deque_pop_back': ('void', 'void *'),
    'list_size': ('unsigned', 'const void *'),  # cover_wake.c: the album cover thread's task list
    'deque_destroy': ('void', 'void *'), 'send': ('int', 'int, const void *, unsigned, int'),
    # stock's name sort, run by name_order.c as toolsQueryDbTable runs it (iterators by value, cstl_iter)
    'deque_begin': ('cstl_iter', 'const void *'), 'deque_end': ('cstl_iter', 'const void *'),
    'algo_sort_if': ('void', 'cstl_iter, cstl_iter, void *'),
    # Coverflow's art thread (coverflow.c)
    'pthread_create': ('int', 'unsigned long *, const void *, void *(*)(void *), void *'),
    'pthread_join': ('int', 'unsigned long, void **'), 'pthread_self': ('unsigned long', 'void'), 'pthread_detach': ('int', 'unsigned long'), 'pthread_mutex_lock': ('int', 'void *'),
    'sem_wait': ('int', 'void *'), 'sem_post': ('int', 'void *'),  # tidal.c: the idle stream threads park on these
    'pthread_mutex_unlock': ('int', 'void *'), 'statfs': ('int', 'const char *, void *'),
    'strdup': ('char *', 'const char *'),
    'strrchr': ('char *', 'const char *, int'), 'strcasecmp': ('int', 'const char *, const char *'),
    'munmap': ('int', 'void *, unsigned'),
    'strcasestr': ('char *', 'const char *, const char *'),
    'strncmp': ('int', 'const char *, const char *, unsigned'),
    'strncasecmp': ('int', 'const char *, const char *, unsigned'),
    # iPod status bar clock (navigation.c)
    'time': ('long', 'long *'),
    'localtime_r': ('const int *', 'const long *, int *'), 'tzset': ('void', 'void'),
    # Cover Flow's open (coverflow.c): stops a cancelled step's statement; demo imports no such function, so it is
    # reached from sqlite3_exec's resolved GOT entry by the rebuilt library's offset, as localtime_r is from tzset's
    'sqlite3_progress_handler': ('void', 'void *, int, int (*)(void *), void *'),
    # Scrobble upload (scrobble.c): demo's libcurl and libcrypto
    'fseek': ('int', 'void *, long, int'), 'ftell': ('long', 'void *'), 'atoi': ('int', 'const char *'),
    'strtol': ('long', 'const char *, char **, int'),  # addon.c progress_row
    'strstr': ('char *', 'const char *, const char *'),
    'curl_easy_init': ('void *', 'void'), 'curl_easy_setopt': ('int', 'void *, int, ...'),
    'curl_easy_perform': ('int', 'void *'), 'curl_easy_getinfo': ('int', 'void *, int, ...'),
    'curl_easy_cleanup': ('void', 'void *'), 'curl_slist_append': ('void *', 'void *, const char *'),
    'curl_slist_free_all': ('void', 'void *'), 'MD5_Init': ('int', 'void *'),
    'MD5_Update': ('int', 'void *, const void *, unsigned'), 'MD5_Final': ('int', 'unsigned char *, void *'),
    'dup2': ('int', 'int, int'),
    '__udivdi3': ('unsigned long long', 'unsigned long long, unsigned long long'),
    # Videos (videos.c): q2video started and waited for, and its key socket
    'fork': ('int', 'void'), 'execl': ('int', 'const char *, const char *, ...'), 'exit': ('void', 'int'),
    'setenv': ('int', 'const char *, const char *, int'),  # addon.c: a card game's Q2_POPUP, in the child
    # Home's Rockbox row (navigation.c rockbox_open)
    'system': ('int', 'const char *'),
    'waitpid': ('int', 'int, int *, int'), '__errno_location': ('int *', 'void'), 'socket': ('int', 'int, int, int'), 'close': ('int', 'int'),
    'connect': ('int', 'int, const void *, unsigned'),
    'recv': ('int', 'int, void *, unsigned, int'),
    'getuid': ('unsigned', 'void'),  # bt_vol_event.c's bus login (SASL EXTERNAL)
    'select': ('int', 'int, void *, void *, void *, void *'),
    'getsockopt': ('int', 'int, int, int, void *, unsigned *'),
    'fcntl': ('int', 'int, int, ...'), 'ioctl': ('int', 'int, unsigned, ...'),
    'usleep': ('int', 'unsigned'),
    'sendto': ('int', 'int, const void *, unsigned, int, const void *, unsigned'),
    # Mapped files, the clock and float maths
    'open': ('int', 'const char *, int, ...'), 'ftruncate': ('int', 'int, long'),
    'mmap': ('void *', 'void *, unsigned, int, int, int, long'),
    'mmap64': ('void *', 'void *, unsigned, int, int, int, long long'),
    'clock_gettime': ('int', 'int, void *'),
    'sinf': ('float', 'float'), 'cosf': ('float', 'float'),
    'write': ('int', 'int, const void *, unsigned'),
}

# The payload's C beside navigation.c (build.py compile_payload), in link order; test/fw builds the same set.
UI_SOURCES = ['peq.c', 'hash.c', 'card_store.c', 'wheel_accel.c', 'peq_ui.c', 'coverflow.c', 'coverflow_thumb.c', 'coverflow_letters.c', 'coverflow_loader.c', 'playlist_art.c', 'slideshow.c', 'library_scope.c', 'scrobble.c', 'videos.c', 'games.c', 'playback.c', 'spotify.c', 'addon.c', 'addon_library.c', 'radio.c', 'tidal.c', 'netupdate.c', 'onlinematch.c', 'crossfade.c', 'nightshift.c', 'headset.c', 'guest.c', 'chapters.c', 'lyrics.c', 'xiph.c', 'airplay.c', 'stock_tls.c', 'splash_hold.c', 'fb_rotate.c',
              'release_sig.c', 'corner_cover.c', 'monocypher/monocypher.c', 'monocypher/monocypher-ed25519.c']
# The UI_SOURCES tools/format.sh leaves alone: upstream's files written before the formatter was adopted, kept
# as upstream writes them so merges of them stay clean, and the vendored monocypher, never reformatted. A new
# file is formatted unless it is added here.
UNFORMATTED = ['peq.c', 'peq_ui.c', 'coverflow.c', 'library_scope.c', 'scrobble.c', 'games.c',
               'playback.c', 'spotify.c', 'addon.c', 'radio.c', 'tidal.c', 'netupdate.c', 'onlinematch.c', 'crossfade.c',
               'nightshift.c', 'headset.c', 'chapters.c', 'lyrics.c', 'airplay.c',
               'monocypher/monocypher.c', 'monocypher/monocypher-ed25519.c']

# sqlite3_progress_handler's place after sqlite3_exec in the rebuilt libsqlite3 (3.54.1, tools/thirdparty.py)
SQLITE_PROGRESS_DELTA = -648348

# libcstl's iterator, 28 bytes, which deque_begin returns and algo_sort_if takes by value
CSTL_ITER = 'typedef struct { unsigned w[7]; } cstl_iter;'

def function_macros(address):
    """Each stock function in address (name -> its C address) as a macro calling it there."""
    return [f'#define {name} (({ret} (*)({args})){address[name]})' for name, (ret, args) in FUNCTIONS.items() if name in address]

def compile_common(out, binary):  # pragma: no mutate block
    got = {}
    for line in run('readelf', '-AW', binary).splitlines():
        words = line.split()
        if len(words) >= 6 and 'UND' in words and re.fullmatch('[0-9a-f]{8}', words[0]):
            got[words[-1].split('@')[0]] = int(words[0], 16)
    gp = GP
    header = ['struct dirent { unsigned ino, off; unsigned short reclen; unsigned char d_type; char d_name[256]; };',
              CSTL_ITER]
    asm = ['.set noreorder', '.text']
    for name, (ret, args) in LIBC.items():
        alias = name
        if name == 'localtime_r':
            libc = symbols(out/'libc-2.28.so')
            delta = libc['localtime_r@@GLIBC_2.0'] - libc['tzset@@GLIBC_2.0']
            check(delta == -7724, 'Unexpected libc localtime_r offset')
            header.append(f'extern {ret} peq_lib_{name}({args});')
            header.append(f'#define {name} peq_lib_{name}')
            address = got['tzset']
            asm += ['.section .scratch,"aw",@nobits', '.align 2',
                    'localtime_r_target: .space 4', f'.section .text.peq_lib_{name},"ax",@progbits',
                    f'.globl peq_lib_{name}', f'peq_lib_{name}:',
                    'lui $t8, %hi(localtime_r_target)', 'lw $t9, %lo(localtime_r_target)($t8)',
                    'bnez $t9, 1f', 'nop',
                    # Resolve tzset's lazy GOT entry once: unlike localtime, it writes no shared buffer.
                    'addiu $sp, $sp, -32', 'sw $ra, 28($sp)', 'sw $a0, 20($sp)', 'sw $a1, 24($sp)',
                    'jal peq_lib_tzset', 'nop', 'lw $ra, 28($sp)', 'lw $a0, 20($sp)',
                    'lw $a1, 24($sp)', 'addiu $sp, $sp, 32',
                    f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}',
                    'lw $t9, 0($t8)', f'addiu $t9, $t9, {delta}',
                    'lui $t8, %hi(localtime_r_target)', 'sw $t9, %lo(localtime_r_target)($t8)',
                    '1:', 'jr $t9', 'nop']
            continue
        if name == 'sqlite3_progress_handler':
            # sqlite3_exec's GOT entry plus the rebuilt libsqlite3's offset between the two (tools/thirdparty.py
            # INSTALL ships it as usr/lib/libsqlite3.so.0.8.6). 0 when the entry is still demo's lazy stub (below
            # 0x1000000, demo's own image): stock_db_open calls it only after its own sqlite3_exec has resolved it.
            import thirdparty
            lib = symbols(thirdparty.build('sqlite')/'usr/lib/libsqlite3.so.3.54.1')
            delta = lib['sqlite3_progress_handler'] - lib['sqlite3_exec']
            check(delta == SQLITE_PROGRESS_DELTA, f'Unexpected libsqlite3 sqlite3_progress_handler offset {delta}')
            header.append(f'extern {ret} peq_lib_{name}({args});')
            header.append(f'#define {name} peq_lib_{name}')
            address = got['sqlite3_exec']
            asm += [f'.section .text.peq_lib_{name},"ax",@progbits', f'.globl peq_lib_{name}', f'peq_lib_{name}:',
                    f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}', 'lw $t9, 0($t8)',
                    'lui $t8, 0x100', 'sltu $t8, $t9, $t8', 'bnez $t8, 1f', 'nop',
                    f'addiu $t9, $t9, {delta}' if -32768 <= delta < 32768 else f'li $t8, {delta}',
                    *([] if -32768 <= delta < 32768 else ['addu $t9, $t9, $t8']),
                    'jr $t9', 'nop', '1:', 'jr $ra', 'nop']
            continue
        if alias not in got:
            header.append(f'extern {ret} {name}({args});')
            continue
        address = got[alias]
        header += [f'extern {ret} peq_lib_{name}({args});', f'#define {name} peq_lib_{name}']
        asm += [f'.section .text.peq_lib_{name},"ax",@progbits', f'.globl peq_lib_{name}',
                f'peq_lib_{name}:', f'lui $gp, {gp >> 16}', f'ori $gp, $gp, {gp & 65535}',
                f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}',
                'lw $t9, 0($t8)', 'jr $t9', 'nop']
        if name in ('memcpy', 'memset', '__udivdi3'):
            asm += [f'.globl {name}', f'.set {name}, peq_lib_{name}']
    syms = symbols(binary) | PRIVATE_FUNCTIONS
    header += function_macros({name: f'0x{syms[name]:x}u' for name in FUNCTIONS if syms.get(name)})
    # build.py has already checked each is a one-byte global.
    header += [f'#define {name} (*(volatile unsigned char *)0x{syms[name]:x}u)' for name in GLOBALS]
    (out/'peq_platform.h').write_text('\n'.join(header)+'\n')
    (out/'peq_imports.S').write_text('\n'.join(asm)+'\n')
    flags = [*FLAGS, '-fno-math-errno', '-ffunction-sections', '-fdata-sections']
    flags += FILL
    objects = []
    for name in UI_SOURCES:
        obj = out/(name.replace('/', '_')+'.o')
        run('clang', *flags, '-I', out, '-c', ROOT/'patch'/name, '-o', obj)
        objects.append(obj)
    obj = out/'peq_imports.o'
    run('clang', *flags, '-c', out/'peq_imports.S', '-o', obj)
    return [*objects, obj]
