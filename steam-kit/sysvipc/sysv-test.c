/* Tests for sysvipc-emu (run under FEX with LD_PRELOAD=<same-arch>/libsysvipc-emu.so).
 *   sysv-test self                 single-arch: fork wait/wake, timeout, SETALL/GETALL, IPC_EXCL, RMID, shm share
 *   sysv-test cross-create KEY     create sem (val 0) + 4 KiB shm with KEY, write a message, wait for the peer's post
 *   sysv-test cross-use KEY        open KEY from the other arch, check the message, post the semaphore
 * Prints PASS/FAIL lines; exit status 0 only when everything passed. */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/sem.h>
#include <sys/shm.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int fails;
#define CHECK(c, what) do { if (c) printf("PASS %s\n", what); else { printf("FAIL %s (errno %d %s)\n", what, errno, strerror(errno)); fails++; } fflush(stdout); } while (0)
union semun { int val; struct semid_ds *buf; unsigned short *array; };
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static int sop(int id, int num, int op, int flg) { struct sembuf b = {(unsigned short)num, (short)op, (short)flg}; return semop(id, &b, 1); }

static void self_test(void) {
    int id = semget(IPC_PRIVATE, 2, IPC_CREAT | 0600); CHECK(id >= 0, "semget private");
    union semun a = {.val = 0}; CHECK(semctl(id, 0, SETVAL, a) == 0 && semctl(id, 0, GETVAL) == 0, "SETVAL/GETVAL");
    pid_t c = fork();
    if (c == 0) { int r = sop(id, 0, -1, 0); _exit(r == 0 ? 0 : 1); }            /* child blocks until the post */
    usleep(200000); CHECK(semctl(id, 0, GETNCNT) == 1, "waiter counted (GETNCNT)");
    double t0 = now(); CHECK(sop(id, 0, 1, 0) == 0, "post"); int st; waitpid(c, &st, 0);
    CHECK(WIFEXITED(st) && WEXITSTATUS(st) == 0 && now() - t0 < 1.0, "child woke after post");
    CHECK(sop(id, 0, -1, IPC_NOWAIT) < 0 && errno == EAGAIN, "IPC_NOWAIT -> EAGAIN");
    struct sembuf b = {0, -1, 0}; struct timespec to = {0, 300000000}; t0 = now();
    CHECK(semtimedop(id, &b, 1, &to) < 0 && errno == EAGAIN && now() - t0 > 0.25, "semtimedop timeout");
    unsigned short in[2] = {3, 7}, out[2] = {0, 0}; union semun s1 = {.array = in}, s2 = {.array = out};
    CHECK(semctl(id, 0, SETALL, s1) == 0 && semctl(id, 0, GETALL, s2) == 0 && out[0] == 3 && out[1] == 7, "SETALL/GETALL");
    struct sembuf two[2] = {{0, -3, 0}, {1, -8, IPC_NOWAIT}};
    CHECK(semop(id, two, 2) < 0 && errno == EAGAIN && semctl(id, 0, GETVAL) == 3, "multi-op is all-or-nothing");
    struct semid_ds ds; union semun s3 = {.buf = &ds}; CHECK(semctl(id, 0, IPC_STAT, s3) == 0 && ds.sem_nsems == 2, "IPC_STAT nsems");
    key_t k = 0x59370000 | (getpid() & 0xffff);
    int kid = semget(k, 1, IPC_CREAT | IPC_EXCL | 0600); CHECK(kid >= 0, "semget key create");
    CHECK(semget(k, 1, IPC_CREAT | IPC_EXCL | 0600) < 0 && errno == EEXIST, "IPC_EXCL -> EEXIST");
    CHECK(semget(k, 1, 0) == kid, "semget key lookup");
    CHECK(semctl(kid, 0, IPC_RMID) == 0 && semget(k, 1, 0) < 0 && errno == ENOENT, "RMID removes the key");
    CHECK(semctl(id, 0, IPC_RMID) == 0, "RMID private");

    int sid = shmget(IPC_PRIVATE, 1 << 20, IPC_CREAT | 0600); CHECK(sid >= 0, "shmget 1 MiB");
    char *p = shmat(sid, NULL, 0); CHECK(p != (void *)-1, "shmat");
    strcpy(p, "parent"); p[(1 << 20) - 1] = 'Z';
    c = fork();
    if (c == 0) { char *q = shmat(sid, NULL, 0); int ok = q != (void *)-1 && !strcmp(q, "parent") && q[(1 << 20) - 1] == 'Z';
                  if (ok) strcpy(q, "child"); shmdt(q); _exit(ok ? 0 : 1); }
    waitpid(c, &st, 0); CHECK(WIFEXITED(st) && WEXITSTATUS(st) == 0 && !strcmp(p, "child"), "shm shared across fork");
    struct shmid_ds sd; CHECK(shmctl(sid, IPC_STAT, &sd) == 0 && sd.shm_segsz == (1 << 20), "shmctl IPC_STAT size");
    CHECK(shmctl(sid, IPC_RMID, NULL) == 0 && p[0] == 'c', "RMID keeps attached mapping");
    CHECK(shmdt(p) == 0, "shmdt");
    CHECK(shmat(sid, NULL, 0) == (void *)-1, "attach after RMID + last detach fails");
}

int main(int argc, char **argv) {
    printf("sysv-test %zu-bit\n", sizeof(void *) * 8);
    if (argc >= 2 && !strcmp(argv[1], "self")) self_test();
    else if (argc >= 3 && !strcmp(argv[1], "cross-create")) {
        key_t k = (key_t)strtoul(argv[2], NULL, 0);
        int id = semget(k, 1, IPC_CREAT | IPC_EXCL | 0600); CHECK(id >= 0, "cross: create sem");
        int sid = shmget(k, 4096, IPC_CREAT | IPC_EXCL | 0600); char *p = shmat(sid, NULL, 0); CHECK(p != (void *)-1, "cross: create shm");
        snprintf(p, 64, "hello from %zu-bit", sizeof(void *) * 8);
        struct sembuf b = {0, -1, 0}; struct timespec to = {20, 0};
        CHECK(semtimedop(id, &b, 1, &to) == 0, "cross: peer posted");
        CHECK(!strncmp(p + 1024, "reply from ", 11), "cross: peer reply in shm"); printf("  peer wrote: %s\n", p + 1024);
        semctl(id, 0, IPC_RMID); shmdt(p); shmctl(sid, IPC_RMID, NULL);
    } else if (argc >= 3 && !strcmp(argv[1], "cross-use")) {
        key_t k = (key_t)strtoul(argv[2], NULL, 0);
        int id = -1, sid = -1; for (int i = 0; i < 100 && (id < 0 || sid < 0); i++) { id = semget(k, 1, 0); sid = shmget(k, 0, 0); if (id < 0 || sid < 0) usleep(100000); }
        CHECK(id >= 0 && sid >= 0, "cross: open by key");
        char *p = shmat(sid, NULL, 0); CHECK(p != (void *)-1 && !strncmp(p, "hello from ", 11), "cross: message visible"); printf("  creator wrote: %s\n", p);
        snprintf(p + 1024, 64, "reply from %zu-bit", sizeof(void *) * 8); shmdt(p);
        CHECK(sop(id, 0, 1, 0) == 0, "cross: post");
    } else { fprintf(stderr, "usage: %s self | cross-create KEY | cross-use KEY\n", argv[0]); return 2; }
    printf("%s (%d failed)\n", fails ? "SYSV_TEST_FAIL" : "SYSV_TEST_PASS", fails);
    return fails ? 1 : 0;
}
