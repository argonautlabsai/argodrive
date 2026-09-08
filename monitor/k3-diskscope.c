/* k3-diskscope — millisecond-granularity per-device read monitor (macOS).
 *
 * Fills the gap both 2026-08-25 reviews flagged: every per-device number in
 * the record is a >=1 s iostat tick averaging ~40 layer-walls (~25 ms each),
 * so intra-burst device behaviour — the "burst model", still marked
 * UNVERIFIED — has never been observed. iostat cannot sample below 1 s;
 * this polls IOKit IOBlockStorageDriver Statistics ("Bytes (Read)",
 * "Operations (Read)") at arbitrary interval and logs cumulative counters
 * per tick to CSV for offline burst-shape analysis.
 *
 * usage: k3-diskscope <interval_ms> <seconds> <out.csv> <bsdname> [bsdname...]
 *   e.g. k3-diskscope 5 130 /tmp/scope.csv disk0 disk5 disk7
 * CSV: t_s,dev,v1,v2,v3 — row meaning by dev:
 *   diskN: v1=bytes_read (cum), v2=ops_read (cum), v3=0
 *   ram:   v1=free_bytes, v2=wired_bytes, v3=compressed_bytes (instant)
 *   cpu:   v1=user_ticks, v2=system_ticks, v3=idle_ticks (cum, all cores)
 *   gpu:   v1=device_util_pct, v2=renderer_util_pct, v3=0 (instant; -1 if
 *          the accelerator exposes no PerformanceStatistics without sudo)
 * Whole-disk BSD names (disk0, not disk0s2) — resolve volumes first via
 *   diskutil info /Volumes/<label> | grep "APFS Physical Store"
 * Read-only; ~0.2% CPU at 5 ms x 3 devices + host stats.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/storage/IOBlockStorageDriver.h>
#include <IOKit/storage/IOMedia.h>
#include <mach/mach.h>
#include <mach/mach_host.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <mach/mach_time.h>

typedef struct {
    const char *name;
    io_registry_entry_t driver; /* IOBlockStorageDriver parent */
} device_t;

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Enumerate IOBlockStorageDriver instances and match the one whose child
 * IOMedia carries the requested BSD name (the whole-disk node, e.g.
 * "disk4"). Walking UP from the media is unreliable: APFS synthesizes
 * zero-counter driver layers that satisfy a naive first-ancestor probe. */
static io_registry_entry_t find_driver_for_bsd(const char *bsd) {
    io_iterator_t drivers = IO_OBJECT_NULL;
    if (IOServiceGetMatchingServices(kIOMainPortDefault,
                                     IOServiceMatching("IOBlockStorageDriver"),
                                     &drivers) != KERN_SUCCESS)
        return IO_OBJECT_NULL;
    io_registry_entry_t driver;
    io_registry_entry_t found = IO_OBJECT_NULL;
    while ((driver = IOIteratorNext(drivers))) {
        io_iterator_t children = IO_OBJECT_NULL;
        if (IORegistryEntryGetChildIterator(driver, kIOServicePlane, &children) ==
            KERN_SUCCESS) {
            io_registry_entry_t child;
            while ((child = IOIteratorNext(children))) {
                CFTypeRef name = IORegistryEntryCreateCFProperty(
                    child, CFSTR("BSD Name"), kCFAllocatorDefault, 0);
                if (name) {
                    char buffer[64] = {0};
                    if (CFGetTypeID(name) == CFStringGetTypeID())
                        CFStringGetCString(name, buffer, sizeof(buffer),
                                           kCFStringEncodingUTF8);
                    CFRelease(name);
                    if (strcmp(buffer, bsd) == 0) {
                        found = driver;
                        IOObjectRetain(found);
                    }
                }
                IOObjectRelease(child);
                if (found) break;
            }
            IOObjectRelease(children);
        }
        IOObjectRelease(driver);
        if (found) break;
    }
    IOObjectRelease(drivers);
    return found;
}

static int read_stats(io_registry_entry_t driver, long long *bytes, long long *ops,
                      long long *busy_ns) {
    CFDictionaryRef stats = IORegistryEntryCreateCFProperty(
        driver, CFSTR(kIOBlockStorageDriverStatisticsKey), kCFAllocatorDefault, 0);
    if (!stats) return -1;
    *bytes = 0;
    *ops = 0;
    *busy_ns = 0;
    CFNumberRef n = CFDictionaryGetValue(stats, CFSTR(kIOBlockStorageDriverStatisticsBytesReadKey));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, bytes);
    n = CFDictionaryGetValue(stats, CFSTR(kIOBlockStorageDriverStatisticsReadsKey));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, ops);
    /* cumulative read service time -> per-window mean op latency and, via
     * Little's law (rate x latency), effective in-flight queue depth */
    n = CFDictionaryGetValue(stats, CFSTR(kIOBlockStorageDriverStatisticsTotalReadTimeKey));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, busy_ns);
    CFRelease(stats);
    return 0;
}

/* Writes: contamination detector for measurement arms (the law requires
 * quiescent engine volumes), K3C copy-campaign progress, and swap-storm
 * localization. Emitted as a second "<dev>w" row per device. */
static int read_write_stats(io_registry_entry_t driver, long long *bytes, long long *ops,
                            long long *busy_ns) {
    CFDictionaryRef stats = IORegistryEntryCreateCFProperty(
        driver, CFSTR(kIOBlockStorageDriverStatisticsKey), kCFAllocatorDefault, 0);
    if (!stats) return -1;
    *bytes = 0;
    *ops = 0;
    *busy_ns = 0;
    CFNumberRef n =
        CFDictionaryGetValue(stats, CFSTR(kIOBlockStorageDriverStatisticsBytesWrittenKey));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, bytes);
    n = CFDictionaryGetValue(stats, CFSTR(kIOBlockStorageDriverStatisticsWritesKey));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, ops);
    n = CFDictionaryGetValue(stats, CFSTR(kIOBlockStorageDriverStatisticsTotalWriteTimeKey));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, busy_ns);
    CFRelease(stats);
    return 0;
}

/* --- host-wide RAM / CPU / GPU samplers ----------------------------------- */

static void sample_ram(long long *free_b, long long *wired_b, long long *compressed_b) {
    vm_statistics64_data_t vm;
    mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
    *free_b = *wired_b = *compressed_b = -1;
    if (host_statistics64(mach_host_self(), HOST_VM_INFO64, (host_info64_t)&vm, &count) ==
        KERN_SUCCESS) {
        long long page = (long long)vm_kernel_page_size;
        *free_b = (long long)vm.free_count * page;
        *wired_b = (long long)vm.wire_count * page;
        /* pageouts (cumulative) replaces compressed-bytes: a rising pageout
         * rate mid-run is the swap-storm alarm that once corrupted an A/B */
        *compressed_b = (long long)vm.pageouts;
    }
}

static void sample_cpu(long long *user_t, long long *system_t, long long *idle_t) {
    host_cpu_load_info_data_t load;
    mach_msg_type_number_t count = HOST_CPU_LOAD_INFO_COUNT;
    *user_t = *system_t = *idle_t = -1;
    if (host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, (host_info_t)&load, &count) ==
        KERN_SUCCESS) {
        *user_t = (long long)load.cpu_ticks[CPU_STATE_USER] + load.cpu_ticks[CPU_STATE_NICE];
        *system_t = (long long)load.cpu_ticks[CPU_STATE_SYSTEM];
        *idle_t = (long long)load.cpu_ticks[CPU_STATE_IDLE];
    }
}

static io_registry_entry_t find_accelerator(void) {
    io_iterator_t it = IO_OBJECT_NULL;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"),
                                     &it) != KERN_SUCCESS)
        return IO_OBJECT_NULL;
    io_registry_entry_t entry = IOIteratorNext(it);
    IOObjectRelease(it);
    return entry; /* first accelerator = the on-die GPU on Apple Silicon */
}

static void sample_gpu(io_registry_entry_t gpu, long long *device_pct, long long *renderer_pct) {
    *device_pct = *renderer_pct = -1;
    if (!gpu) return;
    CFDictionaryRef stats = IORegistryEntryCreateCFProperty(
        gpu, CFSTR("PerformanceStatistics"), kCFAllocatorDefault, 0);
    if (!stats) return;
    CFNumberRef n = CFDictionaryGetValue(stats, CFSTR("Device Utilization %"));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, device_pct);
    n = CFDictionaryGetValue(stats, CFSTR("Renderer Utilization %"));
    if (n) CFNumberGetValue(n, kCFNumberSInt64Type, renderer_pct);
    CFRelease(stats);
}

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: %s <interval_ms> <seconds> <out.csv> <bsdname>...\n", argv[0]);
        return 2;
    }
    int interval_ms = atoi(argv[1]);
    int seconds = atoi(argv[2]);
    const char *out_path = argv[3];
    if (interval_ms < 1 || interval_ms > 1000 || seconds < 1) return 2;

    device_t devices[8];
    int device_count = 0;
    for (int i = 4; i < argc && device_count < 8; i++) {
        /* The IOKit walk can miss a device transiently (observed 2026-09-05:
         * disk0 absent from one sampler's output, disk0+disk4 from the next,
         * with no error surfaced to the dashboard). Retry before giving up. */
        io_registry_entry_t driver = IO_OBJECT_NULL;
        for (int attempt = 0; attempt < 20 && driver == IO_OBJECT_NULL; attempt++) {
            driver = find_driver_for_bsd(argv[i]);
            if (driver == IO_OBJECT_NULL) usleep(50000);
        }
        if (!driver) {
            fprintf(stderr, "no Statistics provider found for %s\n", argv[i]);
            return 1;
        }
        devices[device_count].name = argv[i];
        devices[device_count].driver = driver;
        device_count++;
    }

    io_registry_entry_t gpu = find_accelerator();

    FILE *out = fopen(out_path, "w");
    if (!out) { perror(out_path); return 1; }
    fprintf(out, "t_s,dev,v1,v2,v3\n");

    /* 10 ms burst mode (K3-MONITOR-10MS-UPGRADE-SPEC): the sampler must
     * not be descheduled — QoS user-interactive plus mach_wait_until on
     * ABSOLUTE deadlines replaces usleep's ms-class wakeup slop. */
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    /* Real-time thread class: periodic 10 ms work, ~2 ms budget. This is
     * the canonical macOS fix for sampler wobble; QoS alone leaves ±2 ms
     * scheduler slop on ~3% of ticks. */
    if (interval_ms <= 20) {
        thread_time_constraint_policy_data_t policy;
        const double ns_per_unit =
            (double)timebase.numer / (double)timebase.denom;
        policy.period = (uint32_t)(interval_ms * 1e6 / ns_per_unit);
        policy.computation = (uint32_t)(1e6 / ns_per_unit);      /* 1 ms */
        policy.constraint = (uint32_t)(2e6 / ns_per_unit);       /* 2 ms */
        policy.preemptible = 1;
        thread_policy_set(mach_thread_self(),
                          THREAD_TIME_CONSTRAINT_POLICY,
                          (thread_policy_t)&policy,
                          THREAD_TIME_CONSTRAINT_POLICY_COUNT);
    }
    double start = now_s();
    uint64_t start_mach = mach_absolute_time();
    long long tick = 0;
    while (1) {
        double target = start + (double)(++tick) * interval_ms / 1e3;
        double now = now_s();
        if (now - start >= seconds) break;
        if (target > now) {
            uint64_t deadline = start_mach +
                (uint64_t)((target - start) * 1e9 * timebase.denom /
                           timebase.numer);
            mach_wait_until(deadline);
        }
        double t = now_s() - start;
        for (int i = 0; i < device_count; i++) {
            long long bytes, ops, busy_ns;
            if (read_stats(devices[i].driver, &bytes, &ops, &busy_ns) == 0)
                fprintf(out, "%.4f,%s,%lld,%lld,%lld\n", t, devices[i].name, bytes, ops,
                        busy_ns);
            if (read_write_stats(devices[i].driver, &bytes, &ops, &busy_ns) == 0)
                fprintf(out, "%.4f,%sw,%lld,%lld,%lld\n", t, devices[i].name, bytes, ops,
                        busy_ns);
        }
        long long v1, v2, v3;
        sample_ram(&v1, &v2, &v3);
        fprintf(out, "%.4f,ram,%lld,%lld,%lld\n", t, v1, v2, v3);
        sample_cpu(&v1, &v2, &v3);
        fprintf(out, "%.4f,cpu,%lld,%lld,%lld\n", t, v1, v2, v3);
        sample_gpu(gpu, &v1, &v2);
        fprintf(out, "%.4f,gpu,%lld,%lld,0\n", t, v1, v2);
        /* live mode (coarse intervals, e.g. piped to a dashboard) flushes
         * every tick; 5 ms measurement captures keep the cheap batching */
        if (interval_ms >= 10 || tick % 200 == 0) fflush(out);
    }
    fclose(out);
    if (gpu) IOObjectRelease(gpu);
    for (int i = 0; i < device_count; i++) IOObjectRelease(devices[i].driver);
    return 0;
}
