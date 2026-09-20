/*
 * sysvipc-emu: user-space System V semaphores and shared memory for x86 programs running under FEX on the
 * Lenovo Y700 (Android GKI 6.12 kernel built without CONFIG_SYSVIPC: semget/shmget return ENOSYS).
 * LD_PRELOAD it (i686 and x86-64 builds share the same on-disk format) to replace the libc wrappers:
 *   semget semop semtimedop semctl   shmget shmat shmdt shmctl
 * State lives in /dev/shm/y700-sysv (per-user tmpfs files, mode 0600):
 *   next-id                 id allocator (flock)
 *   sem-key-<key>, shm-key-<key>   key -> id (published with link(), so creation is atomic)
 *   sem-<id>                header + semaphore array, process-shared futex lock and wake counter
 *   shm-<id>, shm-<id>.meta segment data (mmap'ed by shmat) and metadata
 * All shared structures use 32-bit fields only, so i686 and x86-64 processes see the same layout.
 * Limits: no permission checks (single user), SEM_UNDO applied at normal process exit only, no message queues.
 * Y700_SYSV_DEBUG=1 logs every call to stderr.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/futex.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/sem.h>
#include <sys/shm.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define DIR_PATH "/dev/shm/y700-sysv"
#define SEM_MAGIC 0x59375345u /* 'Y7SE' */
#define SHM_MAGIC 0x59375348u /* 'Y7SH' */
#define MAX_SEMS 32000
#define SEMVMX_ 32767
#define ID_BASE 1000

struct sem_hdr {               /* 64 bytes, 32-bit fields only */
    uint32_t magic, version;
    int32_t lock;              /* 0 free, 1 locked, 2 locked + waiters */
    uint32_t seq;              /* bumped on every change; waiters futex-wait on it */
    int32_t removed, nsems, key, mode;
    uint32_t otime, ctime;
    int32_t pad[6];
};
struct sem_ent { int32_t val, pid, ncnt, zcnt; };
struct shm_meta {              /* 64 bytes */
    uint32_t magic, version;
    int32_t lock, removed, key, mode, cpid, lpid, nattch;
    uint32_t size_lo, size_hi, atime, dtime, ctime;
    int32_t pad[2];
};
_Static_assert(sizeof(struct sem_hdr) == 64, "sem_hdr layout");
_Static_assert(sizeof(struct sem_ent) == 16, "sem_ent layout");
_Static_assert(sizeof(struct shm_meta) == 64, "shm_meta layout");

static int dbg = -1;
static void logf_(const char *fmt, ...) {
    if (dbg < 0) { const char *e = getenv("Y700_SYSV_DEBUG"); dbg = e && *e == '1'; }
    if (!dbg) return;
    va_list ap; va_start(ap, fmt); fprintf(stderr, "[sysvipc-emu %d] ", (int)getpid()); vfprintf(stderr, fmt, ap);
    fputc('\n', stderr); va_end(ap);
}

static pthread_mutex_t plock = PTHREAD_MUTEX_INITIALIZER;   /* protects the per-process tables */

/* ---------- futex helpers (shared, not private: the words live in files mapped by several processes) ---------- */
static long futex(int32_t *a, int op, int32_t v, const struct timespec *ts) { return syscall(SYS_futex, a, op, v, ts, NULL, 0); }
static void xlock(int32_t *l) {
    int32_t c = 0;
    if (__atomic_compare_exchange_n(l, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return;
    if (c != 2) c = __atomic_exchange_n(l, 2, __ATOMIC_ACQUIRE);
    while (c != 0) { futex(l, FUTEX_WAIT, 2, NULL); c = __atomic_exchange_n(l, 2, __ATOMIC_ACQUIRE); }
}
static void xunlock(int32_t *l) { if (__atomic_exchange_n(l, 0, __ATOMIC_RELEASE) == 2) futex(l, FUTEX_WAKE, 1, NULL); }
static void bump(uint32_t *seq) { __atomic_add_fetch(seq, 1, __ATOMIC_RELEASE); futex((int32_t *)seq, FUTEX_WAKE, INT_MAX, NULL); }

/* ---------- files ---------- */
static int ensure_dir(void) {
    if (mkdir(DIR_PATH, 0700) == 0 || errno == EEXIST) return 0;
    return -1;
}
static void path(char *out, size_t n, const char *fmt, long v) { char f[64]; snprintf(f, sizeof f, "%s/%s", DIR_PATH, fmt); snprintf(out, n, f, v); }

static int new_id(void) {
    char p[128]; snprintf(p, sizeof p, "%s/next-id", DIR_PATH);
    int fd = open(p, O_RDWR | O_CREAT | O_CLOEXEC, 0600); if (fd < 0) return -1;
    flock(fd, LOCK_EX);
    char b[32] = {0}; long v = ID_BASE; ssize_t r = pread(fd, b, sizeof b - 1, 0);
    if (r > 0) v = strtol(b, NULL, 10); if (v < ID_BASE || v > INT_MAX - 2) v = ID_BASE;
    int n = snprintf(b, sizeof b, "%ld\n", v + 1); ftruncate(fd, 0); pwrite(fd, b, n, 0);
    flock(fd, LOCK_UN); close(fd); return (int)v;
}
static int key_lookup(const char *kind, key_t key) {          /* id or -1 (ENOENT) */
    char p[128], b[32] = {0}; snprintf(p, sizeof p, "%s/%s-key-%08x", DIR_PATH, kind, (unsigned)key);
    int fd = open(p, O_RDONLY | O_CLOEXEC); if (fd < 0) return -1;
    ssize_t r = read(fd, b, sizeof b - 1); close(fd); if (r <= 0) return -1;
    return (int)strtol(b, NULL, 10);
}
static int key_publish(const char *kind, key_t key, int id) {  /* 0, or -1 with EEXIST when another process won */
    char p[128], t[160], b[32]; snprintf(p, sizeof p, "%s/%s-key-%08x", DIR_PATH, kind, (unsigned)key);
    snprintf(t, sizeof t, "%s.%d.tmp", p, (int)getpid());
    int fd = open(t, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600); if (fd < 0) return -1;
    int n = snprintf(b, sizeof b, "%d\n", id); write(fd, b, n); close(fd);
    int r = link(t, p); int e = errno; unlink(t); errno = e; return r;
}
static void key_unlink(const char *kind, key_t key, int id) {
    if (key == IPC_PRIVATE) return;
    if (key_lookup(kind, key) == id) { char p[128]; snprintf(p, sizeof p, "%s/%s-key-%08x", DIR_PATH, kind, (unsigned)key); unlink(p); }
}

/* ---------- semaphores ---------- */
struct semmap { int id; struct sem_hdr *h; size_t len; };
static struct semmap smaps[64]; static int nsmaps;
struct undo { int id; int num; int adj; };
static struct undo undos[256]; static int nundos;

static struct sem_hdr *sem_map(int id) {
    pthread_mutex_lock(&plock);
    for (int i = 0; i < nsmaps; i++) if (smaps[i].id == id) { struct sem_hdr *h = smaps[i].h; pthread_mutex_unlock(&plock); return h; }
    char p[128]; path(p, sizeof p, "sem-%ld", id);
    int fd = open(p, O_RDWR | O_CLOEXEC); struct stat st;
    if (fd < 0 || fstat(fd, &st) < 0 || st.st_size < (off_t)sizeof(struct sem_hdr)) { if (fd >= 0) close(fd); pthread_mutex_unlock(&plock); errno = EINVAL; return NULL; }
    void *m = mmap(NULL, st.st_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0); close(fd);
    if (m == MAP_FAILED) { pthread_mutex_unlock(&plock); return NULL; }
    struct sem_hdr *h = m;
    if (h->magic != SEM_MAGIC) { munmap(m, st.st_size); pthread_mutex_unlock(&plock); errno = EINVAL; return NULL; }
    if (nsmaps == 64) { munmap(smaps[0].h, smaps[0].len); memmove(smaps, smaps + 1, 63 * sizeof *smaps); nsmaps--; }
    smaps[nsmaps++] = (struct semmap){id, h, (size_t)st.st_size};
    pthread_mutex_unlock(&plock); return h;
}
static void sem_forget(int id) {
    pthread_mutex_lock(&plock);
    for (int i = 0; i < nsmaps; i++) if (smaps[i].id == id) { munmap(smaps[i].h, smaps[i].len); smaps[i] = smaps[--nsmaps]; break; }
    pthread_mutex_unlock(&plock);
}
static struct sem_ent *ents(struct sem_hdr *h) { return (struct sem_ent *)(h + 1); }

static int sem_create(key_t key, int nsems, int flg) {
    if (nsems <= 0 || nsems > MAX_SEMS) { errno = EINVAL; return -1; }
    int id = new_id(); if (id < 0) return -1;
    char p[128], t[160]; path(p, sizeof p, "sem-%ld", id); snprintf(t, sizeof t, "%s.tmp", p);
    int fd = open(t, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600); if (fd < 0) return -1;
    size_t len = sizeof(struct sem_hdr) + (size_t)nsems * sizeof(struct sem_ent);
    if (ftruncate(fd, len) < 0) { close(fd); unlink(t); return -1; }
    struct sem_hdr h = {SEM_MAGIC, 1, 0, 0, 0, nsems, (int32_t)key, flg & 0777, 0, (uint32_t)time(NULL), {0}};
    pwrite(fd, &h, sizeof h, 0); close(fd);
    if (rename(t, p) < 0) { unlink(t); return -1; }
    if (key != IPC_PRIVATE && key_publish("sem", key, id) < 0) { int e = errno; unlink(p); errno = e; return -1; }
    return id;
}

int semget(key_t key, int nsems, int semflg) {
    if (ensure_dir() < 0) return -1;
    int r;
    if (key == IPC_PRIVATE) r = sem_create(key, nsems, semflg);
    else for (int tries = 0;; tries++) {
        int id = key_lookup("sem", key);
        if (id >= 0) {
            struct sem_hdr *h = sem_map(id);
            if (!h || h->removed) { if (h) sem_forget(id); key_unlink("sem", key, id); if (tries < 3) continue; errno = ENOENT; r = -1; break; }
            if ((semflg & IPC_CREAT) && (semflg & IPC_EXCL)) { errno = EEXIST; r = -1; break; }
            if (nsems > h->nsems) { errno = EINVAL; r = -1; break; }
            r = id; break;
        }
        if (!(semflg & IPC_CREAT)) { errno = ENOENT; r = -1; break; }
        r = sem_create(key, nsems, semflg);
        if (r < 0 && errno == EEXIST && tries < 3) continue;        /* lost the race: use the winner's id */
        break;
    }
    logf_("semget(key=0x%x, nsems=%d, flg=0%o) = %d%s", (unsigned)key, nsems, semflg, r, r < 0 ? strerror(errno) : "");
    return r;
}

static void add_undo(int id, int num, int adj) {
    pthread_mutex_lock(&plock);
    for (int i = 0; i < nundos; i++) if (undos[i].id == id && undos[i].num == num) { undos[i].adj += adj; pthread_mutex_unlock(&plock); return; }
    if (nundos < 256) undos[nundos++] = (struct undo){id, num, adj};
    pthread_mutex_unlock(&plock);
}

int semtimedop(int semid, struct sembuf *sops, size_t nsops, const struct timespec *timeout) {
    struct sem_hdr *h = sem_map(semid); if (!h) { errno = EINVAL; return -1; }
    if (nsops == 0 || nsops > 500) { errno = E2BIG; return -1; }
    struct timespec end = {0, 0}, now;
    if (timeout) { clock_gettime(CLOCK_MONOTONIC, &end); end.tv_sec += timeout->tv_sec; end.tv_nsec += timeout->tv_nsec; if (end.tv_nsec >= 1000000000) { end.tv_sec++; end.tv_nsec -= 1000000000; } }
    for (size_t i = 0; i < nsops; i++) if (sops[i].sem_num >= (unsigned)h->nsems) { errno = EFBIG; return -1; }
    for (;;) {
        xlock(&h->lock);
        if (h->removed) { xunlock(&h->lock); errno = EIDRM; return -1; }
        struct sem_ent *e = ents(h); int blocked = -1, bzero_ = 0, nowait = 0;
        int32_t tmp[500]; unsigned short idx[500]; int nt = 0; int ok = 1;     /* simulate on a copy of touched sems */
        for (size_t i = 0; i < nsops && ok; i++) {
            int n = sops[i].sem_num, j; for (j = 0; j < nt && idx[j] != n; j++) ;
            if (j == nt) { idx[nt] = n; tmp[nt++] = e[n].val; }
            int op = sops[i].sem_op;
            if (op > 0) { if (tmp[j] + op > SEMVMX_) { xunlock(&h->lock); errno = ERANGE; return -1; } tmp[j] += op; }
            else if (op == 0) { if (tmp[j] != 0) { ok = 0; blocked = n; bzero_ = 1; nowait = sops[i].sem_flg & IPC_NOWAIT; } }
            else { if (tmp[j] + op < 0) { ok = 0; blocked = n; nowait = sops[i].sem_flg & IPC_NOWAIT; } else tmp[j] += op; }
        }
        if (ok) {
            for (int j = 0; j < nt; j++) { e[idx[j]].val = tmp[j]; e[idx[j]].pid = getpid(); }
            h->otime = (uint32_t)time(NULL);
            xunlock(&h->lock); bump(&h->seq);
            for (size_t i = 0; i < nsops; i++) if (sops[i].sem_flg & SEM_UNDO) add_undo(semid, sops[i].sem_num, -sops[i].sem_op);
            logf_("semop(%d, n=%zu) ok", semid, nsops);
            return 0;
        }
        if (nowait) { xunlock(&h->lock); errno = EAGAIN; return -1; }
        if (bzero_) e[blocked].zcnt++; else e[blocked].ncnt++;
        uint32_t s = __atomic_load_n(&h->seq, __ATOMIC_ACQUIRE); xunlock(&h->lock);
        struct timespec rel, *prel = NULL;
        if (timeout) {
            clock_gettime(CLOCK_MONOTONIC, &now);
            long long ns = (long long)(end.tv_sec - now.tv_sec) * 1000000000LL + (end.tv_nsec - now.tv_nsec);
            if (ns <= 0) ns = 0; rel.tv_sec = ns / 1000000000LL; rel.tv_nsec = ns % 1000000000LL; prel = &rel;
        }
        long fr = (prel && prel->tv_sec == 0 && prel->tv_nsec == 0) ? (errno = ETIMEDOUT, -1) : futex((int32_t *)&h->seq, FUTEX_WAIT, (int32_t)s, prel);
        int fe = errno;
        xlock(&h->lock); if (bzero_) e[blocked].zcnt--; else e[blocked].ncnt--; xunlock(&h->lock);
        if (fr < 0 && fe == ETIMEDOUT) { errno = EAGAIN; return -1; }
        if (fr < 0 && fe == EINTR) { errno = EINTR; return -1; }
    }
}
int semop(int semid, struct sembuf *sops, size_t nsops) { return semtimedop(semid, sops, nsops, NULL); }

union semun_ { int val; struct semid_ds *buf; unsigned short *array; struct seminfo *info; void *p; };

int semctl(int semid, int semnum, int cmd, ...) {
    union semun_ arg = {0}; va_list ap; va_start(ap, cmd); arg.p = va_arg(ap, void *); va_end(ap);
    cmd &= ~0x100;                                                    /* IPC_64 */
    if (cmd == IPC_INFO || cmd == SEM_INFO) {
        if (arg.info) { struct seminfo *si = arg.info; memset(si, 0, sizeof *si); si->semmni = 32000; si->semmsl = MAX_SEMS;
            si->semmns = 1024000000; si->semopm = 500; si->semvmx = SEMVMX_; si->semaem = SEMVMX_; si->semmap = 32000; si->semume = 500; }
        return 0;
    }
    struct sem_hdr *h = sem_map(semid); if (!h) { errno = EINVAL; return -1; }
    struct sem_ent *e = ents(h); int r = 0;
    if ((cmd == GETVAL || cmd == SETVAL || cmd == GETPID || cmd == GETNCNT || cmd == GETZCNT) && (semnum < 0 || semnum >= h->nsems)) { errno = EINVAL; return -1; }
    xlock(&h->lock);
    if (h->removed) { xunlock(&h->lock); errno = EIDRM; return -1; }
    switch (cmd) {
    case IPC_RMID: {
        h->removed = 1; int32_t key = h->key; xunlock(&h->lock); bump(&h->seq);
        char p[128]; path(p, sizeof p, "sem-%ld", semid); unlink(p); key_unlink("sem", key, semid); sem_forget(semid);
        logf_("semctl(%d, IPC_RMID)", semid); return 0; }
    case IPC_STAT: case SEM_STAT: {
        struct semid_ds *d = arg.buf; if (!d) { r = -1; errno = EFAULT; break; }
        memset(d, 0, sizeof *d); d->sem_perm.__key = h->key; d->sem_perm.uid = d->sem_perm.cuid = getuid();
        d->sem_perm.gid = d->sem_perm.cgid = getgid(); d->sem_perm.mode = h->mode; d->sem_nsems = h->nsems;
        d->sem_otime = h->otime; d->sem_ctime = h->ctime; if (cmd == SEM_STAT) r = semid; break; }
    case IPC_SET: if (arg.buf) h->mode = arg.buf->sem_perm.mode & 0777; h->ctime = (uint32_t)time(NULL); break;
    case GETVAL: r = e[semnum].val; break;
    case GETPID: r = e[semnum].pid; break;
    case GETNCNT: r = e[semnum].ncnt; break;
    case GETZCNT: r = e[semnum].zcnt; break;
    case SETVAL:
        if (arg.val < 0 || arg.val > SEMVMX_) { r = -1; errno = ERANGE; break; }
        e[semnum].val = arg.val; h->ctime = (uint32_t)time(NULL); xunlock(&h->lock); bump(&h->seq);
        logf_("semctl(%d, %d, SETVAL, %d)", semid, semnum, arg.val); return 0;
    case GETALL: if (!arg.array) { r = -1; errno = EFAULT; break; } for (int i = 0; i < h->nsems; i++) arg.array[i] = (unsigned short)e[i].val; break;
    case SETALL:
        if (!arg.array) { r = -1; errno = EFAULT; break; }
        for (int i = 0; i < h->nsems; i++) if (arg.array[i] > SEMVMX_) { r = -1; errno = ERANGE; break; }
        if (r == 0) { for (int i = 0; i < h->nsems; i++) e[i].val = arg.array[i]; h->ctime = (uint32_t)time(NULL); xunlock(&h->lock); bump(&h->seq); return 0; }
        break;
    default: r = -1; errno = EINVAL;
    }
    xunlock(&h->lock);
    logf_("semctl(%d, %d, cmd=%d) = %d", semid, semnum, cmd, r);
    return r;
}

__attribute__((destructor)) static void apply_undos(void) {              /* SEM_UNDO at normal exit */
    for (int i = 0; i < nundos; i++) {
        if (!undos[i].adj) continue;
        struct sem_hdr *h = sem_map(undos[i].id); if (!h) continue;
        xlock(&h->lock);
        if (!h->removed && undos[i].num < h->nsems) { int32_t v = ents(h)[undos[i].num].val + undos[i].adj; ents(h)[undos[i].num].val = v < 0 ? 0 : v > SEMVMX_ ? SEMVMX_ : v; }
        xunlock(&h->lock); bump(&h->seq);
    }
}

/* ---------- shared memory ---------- */
struct att { void *addr; size_t len; int id; };
static struct att atts[256]; static int natts;

static int meta_open(int id, struct shm_meta **m) {
    char p[128]; path(p, sizeof p, "shm-%ld.meta", id);
    int fd = open(p, O_RDWR | O_CLOEXEC); if (fd < 0) { errno = EINVAL; return -1; }
    void *v = mmap(NULL, sizeof **m, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0); close(fd);
    if (v == MAP_FAILED) return -1;
    if (((struct shm_meta *)v)->magic != SHM_MAGIC) { munmap(v, sizeof **m); errno = EINVAL; return -1; }
    *m = v;
    return 0;
}
static size_t meta_size(struct shm_meta *m) { return (size_t)m->size_lo | (sizeof(size_t) > 4 ? ((size_t)m->size_hi << 16 << 16) : 0); }
static void shm_unlink_all(int id) {
    char p[128]; path(p, sizeof p, "shm-%ld", id); unlink(p); path(p, sizeof p, "shm-%ld.meta", id); unlink(p);
}

static int shm_create(key_t key, size_t size, int flg) {
    if (size == 0) { errno = EINVAL; return -1; }
    int id = new_id(); if (id < 0) return -1;
    char p[128], q[128]; path(p, sizeof p, "shm-%ld", id); path(q, sizeof q, "shm-%ld.meta", id);
    int fd = open(p, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600); if (fd < 0) return -1;
    if (ftruncate(fd, size) < 0) { close(fd); unlink(p); return -1; } close(fd);
    struct shm_meta m = {SHM_MAGIC, 1, 0, 0, (int32_t)key, flg & 0777, getpid(), 0, 0, (uint32_t)(size & 0xffffffffu),
                         (uint32_t)((unsigned long long)size >> 32), 0, 0, (uint32_t)time(NULL), {0}};
    char t[160]; snprintf(t, sizeof t, "%s.tmp", q);
    int mf = open(t, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600); if (mf < 0) { unlink(p); return -1; }
    pwrite(mf, &m, sizeof m, 0); close(mf);
    if (rename(t, q) < 0) { unlink(t); unlink(p); return -1; }
    if (key != IPC_PRIVATE && key_publish("shm", key, id) < 0) { int e = errno; shm_unlink_all(id); errno = e; return -1; }
    return id;
}

int shmget(key_t key, size_t size, int shmflg) {
    if (ensure_dir() < 0) return -1;
    int r;
    if (key == IPC_PRIVATE) r = shm_create(key, size, shmflg);
    else for (int tries = 0;; tries++) {
        int id = key_lookup("shm", key);
        if (id >= 0) {
            struct shm_meta *m = NULL;
            if (meta_open(id, &m) < 0 || m->removed) {                   /* stale key file */
                if (m) munmap(m, sizeof *m);
                key_unlink("shm", key, id); if (tries < 3) continue; errno = ENOENT; r = -1; break;
            }
            size_t have = meta_size(m); munmap(m, sizeof *m);
            if ((shmflg & IPC_CREAT) && (shmflg & IPC_EXCL)) { errno = EEXIST; r = -1; break; }
            if (size > have) { errno = EINVAL; r = -1; break; }
            r = id; break;
        }
        if (!(shmflg & IPC_CREAT)) { errno = ENOENT; r = -1; break; }
        r = shm_create(key, size, shmflg);
        if (r < 0 && errno == EEXIST && tries < 3) continue;
        break;
    }
    logf_("shmget(key=0x%x, size=%zu, flg=0%o) = %d%s", (unsigned)key, size, shmflg, r, r < 0 ? strerror(errno) : "");
    return r;
}

void *shmat(int shmid, const void *shmaddr, int shmflg) {
    struct shm_meta *m; if (meta_open(shmid, &m) < 0) return (void *)-1;
    if (m->removed && m->nattch == 0) { munmap(m, sizeof *m); errno = EIDRM; return (void *)-1; }
    size_t len = meta_size(m);
    char p[128]; path(p, sizeof p, "shm-%ld", shmid);
    int ro = shmflg & SHM_RDONLY; int fd = open(p, (ro ? O_RDONLY : O_RDWR) | O_CLOEXEC);
    if (fd < 0) { munmap(m, sizeof *m); errno = EIDRM; return (void *)-1; }
    int flags = MAP_SHARED; void *want = (void *)shmaddr;
    if (want) { if (shmflg & SHM_RND) want = (void *)((uintptr_t)want & ~(uintptr_t)(SHMLBA - 1)); flags |= MAP_FIXED; }
    void *a = mmap(want, len, PROT_READ | (ro ? 0 : PROT_WRITE), flags, fd, 0); int e = errno; close(fd);
    if (a == MAP_FAILED) { munmap(m, sizeof *m); errno = e == ENOMEM ? ENOMEM : EINVAL; return (void *)-1; }
    xlock(&m->lock); m->nattch++; m->lpid = getpid(); m->atime = (uint32_t)time(NULL); xunlock(&m->lock); munmap(m, sizeof *m);
    pthread_mutex_lock(&plock); if (natts < 256) atts[natts++] = (struct att){a, len, shmid}; pthread_mutex_unlock(&plock);
    logf_("shmat(%d) = %p len %zu", shmid, a, len);
    return a;
}

int shmdt(const void *shmaddr) {
    pthread_mutex_lock(&plock); int i;
    for (i = 0; i < natts && atts[i].addr != shmaddr; i++) ;
    if (i == natts) { pthread_mutex_unlock(&plock); errno = EINVAL; return -1; }
    struct att a = atts[i]; atts[i] = atts[--natts]; pthread_mutex_unlock(&plock);
    munmap(a.addr, a.len);
    struct shm_meta *m;
    if (meta_open(a.id, &m) == 0) {
        xlock(&m->lock); m->nattch--; m->lpid = getpid(); m->dtime = (uint32_t)time(NULL); int gone = m->removed && m->nattch <= 0; xunlock(&m->lock);
        munmap(m, sizeof *m); if (gone) shm_unlink_all(a.id);
    }
    logf_("shmdt(%p) id %d", shmaddr, a.id);
    return 0;
}

int shmctl(int shmid, int cmd, struct shmid_ds *buf) {
    cmd &= ~0x100;
    if (cmd == IPC_INFO || cmd == SHM_INFO) { if (buf) memset(buf, 0, sizeof *buf); return 0; }
    struct shm_meta *m; if (meta_open(shmid, &m) < 0) return -1;
    int r = 0;
    xlock(&m->lock);
    switch (cmd) {
    case IPC_RMID: {
        m->removed = 1; int32_t key = m->key; int gone = m->nattch <= 0; xunlock(&m->lock); munmap(m, sizeof *m);
        key_unlink("shm", key, shmid); if (gone) shm_unlink_all(shmid);
        logf_("shmctl(%d, IPC_RMID) attached=%d", shmid, !gone); return 0; }
    case IPC_STAT: case SHM_STAT:
        if (!buf) { r = -1; errno = EFAULT; break; }
        memset(buf, 0, sizeof *buf); buf->shm_perm.__key = m->key; buf->shm_perm.uid = buf->shm_perm.cuid = getuid();
        buf->shm_perm.gid = buf->shm_perm.cgid = getgid(); buf->shm_perm.mode = m->mode | (m->removed ? SHM_DEST : 0);
        buf->shm_segsz = meta_size(m); buf->shm_nattch = m->nattch; buf->shm_cpid = m->cpid; buf->shm_lpid = m->lpid;
        buf->shm_atime = m->atime; buf->shm_dtime = m->dtime; buf->shm_ctime = m->ctime; if (cmd == SHM_STAT) r = shmid; break;
    case IPC_SET: if (buf) m->mode = buf->shm_perm.mode & 0777; m->ctime = (uint32_t)time(NULL); break;
    case SHM_LOCK: case SHM_UNLOCK: break;
    default: r = -1; errno = EINVAL;
    }
    xunlock(&m->lock); munmap(m, sizeof *m);
    logf_("shmctl(%d, cmd=%d) = %d", shmid, cmd, r);
    return r;
}
