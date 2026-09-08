/* Transient memory-pressure tool (persistent rebuild of the session-scratchpad
 * pattern lost in the 2026-08-22 battery panic). Allocates N GiB, touches every
 * page so the kernel must reclaim cache into the free list, then exits and
 * releases it all. Usage: k3-pressure <GiB>. Plain C on purpose: CLT clang 14
 * cannot parse the 26.4 SDK's libc++, but C is fine.
 *
 * 2026-08-31 CORRECTNESS FIX. The previous build was a NO-OP. `memset(p, 1, n);
 * free(p);` writes memory that is never read before it is freed, so the whole
 * sequence is dead code and clang -O2 deleted it: the 2026-08-22 binary's
 * undefined-symbol table contained neither _malloc nor _memset, and `main`
 * validated its argument and returned 0 in ~2 ms. Every block from 2026-08-22
 * to 2026-08-31 therefore ran with UNCLEARED cache, inheriting whatever the
 * previous arm left warm. ABBA ordering absorbs some of that; ladders do not.
 *
 * The fix has two halves and both are load-bearing:
 *   - `volatile` on the pointer, so each page store must actually be emitted;
 *   - an observable use of the touched bytes (`sum`) checked after the free,
 *     so the optimizer cannot prove the work is dead and delete it again.
 * Verify after any rebuild with:  nm -u k3-pressure | grep -c malloc   -> 1
 */
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <GiB>\n", argv[0]); return 2; }
    size_t gib = (size_t)atoi(argv[1]);
    if (gib == 0 || gib > 120) { fprintf(stderr, "bad GiB\n"); return 2; }
    size_t n = gib << 30;
    volatile unsigned char *p = malloc(n);
    if (!p) { fprintf(stderr, "malloc failed\n"); return 1; }

    size_t page = (size_t)getpagesize();
    unsigned long long sum = 0;
    for (size_t i = 0; i < n; i += page) {
        p[i] = 1;
        sum += p[i];
    }
    free((void *)p);

    /* Observable use. Without it the loop above is dead code — which is
     * precisely how the previous build came to do nothing at all. */
    if (sum == 0) { fprintf(stderr, "pressure did not take effect\n"); return 1; }
    return 0;
}
