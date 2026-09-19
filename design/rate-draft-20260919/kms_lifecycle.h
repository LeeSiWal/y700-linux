#ifndef Y700_KMS_LIFECYCLE_H
#define Y700_KMS_LIFECYCLE_H
#include <stdbool.h>
#include <string.h>
enum kms_outcome { KMS_TEST_FAILED, KMS_GATE_REFUSED, KMS_APPLY_FAILED, KMS_COMMIT_RETURNED };
struct kms_actions { void *ctx; int (*test)(void *); bool (*gate)(void *); int (*apply)(void *); };
static bool kms_gate_matches(const char *line,bool interrupted) { return !interrupted && line && strcmp(line,"GO\n")==0; }
/* Exactly one possible apply call. No restore/disable/cleanup callback exists. */
static enum kms_outcome kms_once(struct kms_actions *a) {
 if(a->test(a->ctx)<0)return KMS_TEST_FAILED;
 if(!a->gate(a->ctx))return KMS_GATE_REFUSED;
 if(a->apply(a->ctx)<0)return KMS_APPLY_FAILED;
 return KMS_COMMIT_RETURNED;
}
#endif
