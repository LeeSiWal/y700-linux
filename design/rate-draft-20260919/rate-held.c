/* Back-to-back two-plane flip rate test on the inherited DRM file.
 * No allocation and no CPU pixel writes: reuses FB341/FB342 that the flip test
 * created, CPU-verified and user-confirmed, then returns to FB335. Each step is
 * one blocking atomic commit changing only FB_ID on plane95+plane128 together,
 * with PAGE_FLIP_EVENT consumed before the next. Timing uses CLOCK_MONOTONIC;
 * the vendor event carries no usable vblank sequence/timestamp.
 * Any error/timeout/signal holds every resource: no close, no restore, no retry. */
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <drm/drm_fourcc.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>
#include "kms_lifecycle.h"
#define N 256
#define W 1904
#define H 3040
#define HALF 952
#define CONFIRMED_FB 335
#define FB_A 341
#define FB_B 342
#define MAX_STEPS 1200
#define EVENT_TIMEOUT_MS 200   /* ~24 frames; a blocked flip is a STOP, not a slow sample */
#define SLOW_US 12500          /* > 1.5 frames at 120Hz counts as a missed frame */
static int fd=-1;
static volatile sig_atomic_t stop;
static void interrupted(int sig){(void)sig;stop=1;}
static void hold(const char *reason) __attribute__((noreturn));
static void hold(const char *reason){
 printf("HELD pid=%d reason=%s\n",getpid(),reason);fflush(stdout);
 for(;;)sleep(1); /* FD and framebuffers deliberately remain owned. */
}
static void fail(const char *msg) __attribute__((noreturn));
static void fail(const char *msg){fprintf(stderr,"STOP: %s\n",msg);if(fd>=0)hold(msg);exit(2);}
static void require(bool ok,const char *msg){if(!ok)fail(msg);}
static void checked(unsigned long request,void *arg,const char *msg){if(ioctl(fd,request,arg)<0){perror(msg);fail(msg);}}
struct properties {uint32_t n,id[N];uint64_t val[N];char name[N][DRM_PROP_NAME_LEN+1];};
static struct properties properties(uint32_t id,uint32_t type){
 struct properties p={0};struct drm_mode_obj_get_properties q={.obj_id=id,.obj_type=type};
 checked(DRM_IOCTL_MODE_OBJ_GETPROPERTIES,&q,"property count");require(q.count_props<=N,"too many properties");
 q.props_ptr=(uintptr_t)p.id;q.prop_values_ptr=(uintptr_t)p.val;
 checked(DRM_IOCTL_MODE_OBJ_GETPROPERTIES,&q,"properties");require(q.count_props<=N,"property list changed");p.n=q.count_props;
 for(uint32_t i=0;i<p.n;i++){struct drm_mode_get_property g={.prop_id=p.id[i]};checked(DRM_IOCTL_MODE_GETPROPERTY,&g,"property name");memcpy(p.name[i],g.name,DRM_PROP_NAME_LEN);}
 return p;
}
static int indexof(const struct properties *p,const char *name){for(uint32_t i=0;i<p->n;i++)if(!strcmp(p->name[i],name))return (int)i;return -1;}
static uint64_t value(const struct properties *p,const char *name){int i=indexof(p,name);require(i>=0,name);return p->val[i];}
static void geometry(const struct properties *p,uint32_t framebuffer,uint32_t sx,uint32_t dx){
 require(value(p,"FB_ID")==framebuffer && value(p,"CRTC_ID")==205 && value(p,"SRC_X")==((uint64_t)sx<<16) && value(p,"SRC_Y")==0 && value(p,"SRC_W")==((uint64_t)HALF<<16) && value(p,"SRC_H")==((uint64_t)H<<16),"plane source changed");
 require(value(p,"CRTC_X")==dx && value(p,"CRTC_Y")==0 && value(p,"CRTC_W")==HALF && value(p,"CRTC_H")==H,"plane destination changed");
}
static void existing_fb(uint32_t id){
 struct drm_mode_fb_cmd2 f={.fb_id=id};checked(DRM_IOCTL_MODE_GETFB2,&f,"retained framebuffer missing");
 require(f.width==W && f.height==H && f.pitches[0]==7680 && f.pixel_format==DRM_FORMAT_XRGB8888 && f.modifier[0]==0 && f.offsets[0]==0,"retained framebuffer layout changed");
 printf("RETAINED_FB id=%u %ux%u pitch=%u\n",id,f.width,f.height,f.pitches[0]);
}
struct flip {uint32_t objects[2],counts[2],ids[2];uint64_t values[2];};
static uint32_t left_prop,right_prop;
static int commit(uint32_t fb,uint32_t flags,uint64_t user){
 struct flip f={.objects={95,128},.counts={1,1},.ids={left_prop,right_prop},.values={fb,fb}};
 struct drm_mode_atomic a={.flags=flags,.count_objs=2,.objs_ptr=(uintptr_t)f.objects,.count_props_ptr=(uintptr_t)f.counts,.props_ptr=(uintptr_t)f.ids,.prop_values_ptr=(uintptr_t)f.values,.user_data=user};
 return ioctl(fd,DRM_IOCTL_MODE_ATOMIC,&a);
}
static void wait_event(uint64_t user){
 struct pollfd pfd={.fd=fd,.events=POLLIN};int rc=poll(&pfd,1,EVENT_TIMEOUT_MS);
 require(rc==1 && (pfd.revents&POLLIN),"flip event timeout; state uncertain");
 struct drm_event_vblank ev;ssize_t got=read(fd,&ev,sizeof(ev));
 require(got==(ssize_t)sizeof(ev) && ev.base.type==DRM_EVENT_FLIP_COMPLETE && ev.base.length==sizeof(ev),"unexpected DRM event");
 require(ev.user_data==user && ev.crtc_id==205,"event for another commit/CRTC");
}
static uint64_t now_us(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000u+(uint64_t)t.tv_nsec/1000u;}
static unsigned steps;
static uint32_t fb_at(unsigned k){return k>steps?CONFIRMED_FB:((k&1)?FB_A:FB_B);} /* k is 1-based */
static uint64_t t_start,t_done[MAX_STEPS+2];
static unsigned completed;
static int phase_test(void *ctx){(void)ctx;if(stop)return -1;
 const uint32_t fbs[]={FB_A,FB_B,CONFIRMED_FB};
 for(unsigned i=0;i<3;i++)if(commit(fbs[i],DRM_MODE_ATOMIC_TEST_ONLY,0)<0){perror("ATOMIC TEST_ONLY");return -1;}
 return 0;}
static bool phase_gate(void *ctx){(void)ctx;char gate[16]={0};puts("TEST_ONLY_PASS");puts("READY_FOR_RATE");if(!fgets(gate,sizeof(gate),stdin))return false;return kms_gate_matches(gate,stop!=0);}
static int phase_apply(void *ctx){(void)ctx;
 puts("RATE_ENTER");t_start=now_us();
 /* Tight loop: no output until the last commit returns, so logging does not pace it. */
 for(unsigned k=1;k<=steps+1;k++){
  if(stop)return -1;
  if(commit(fb_at(k),DRM_MODE_PAGE_FLIP_EVENT,k)<0){perror("ATOMIC flip");printf("RATE_FAILED_AT %u fb=%u\n",k,fb_at(k));return -1;}
  wait_event(k);t_done[k]=now_us();completed=k;
 }
 return 0;
}
int main(int argc,char **argv){
 require(argc==5 && !strcmp(argv[1],"--inherited-fd") && !strcmp(argv[3],"--steps"),"usage: rate-held --inherited-fd N --steps 120|1200");
 char *end=NULL;long inherited=strtol(argv[2],&end,10);require(end && !*end && inherited>=3 && inherited<=1024,"invalid descriptor");fd=(int)inherited;
 require(!strcmp(argv[4],"120") || !strcmp(argv[4],"1200"),"steps must be 120 or 1200");steps=(unsigned)atoi(argv[4]);
 setbuf(stdout,NULL);signal(SIGINT,interrupted);signal(SIGTERM,interrupted);signal(SIGHUP,interrupted);signal(SIGPIPE,SIG_IGN);
 struct stat st;require(fstat(fd,&st)==0 && S_ISCHR(st.st_mode) && major(st.st_rdev)==226 && minor(st.st_rdev)==0,"wrong card0 descriptor");
 puts("INHERITED_DRM_FILE all retained workers and FB78/FB335/FB341/FB342 kept; no new DRM open; no allocation");
 struct properties left=properties(95,DRM_MODE_OBJECT_PLANE),right=properties(128,DRM_MODE_OBJECT_PLANE),crtc=properties(205,DRM_MODE_OBJECT_CRTC),conn=properties(69,DRM_MODE_OBJECT_CONNECTOR);
 require(value(&conn,"CRTC_ID")==205 && value(&crtc,"ACTIVE")==1 && value(&crtc,"MODE_ID")==340,"native display state changed");
 geometry(&left,CONFIRMED_FB,0,0);geometry(&right,CONFIRMED_FB,HALF,HALF);
 require(value(&left,"zpos")==0 && value(&right,"zpos")==1,"plane ordering changed");
 left_prop=left.id[indexof(&left,"FB_ID")];right_prop=right.id[indexof(&right,"FB_ID")];
 existing_fb(FB_A);existing_fb(FB_B);existing_fb(CONFIRMED_FB);
 printf("PLAN steps=%u back-to-back blocking atomic FB_ID-only on planes95+128; final return to FB%d\n",steps,CONFIRMED_FB);
 struct kms_actions actions={.ctx=NULL,.test=phase_test,.gate=phase_gate,.apply=phase_apply};enum kms_outcome outcome=kms_once(&actions);
 if(outcome==KMS_TEST_FAILED)hold("TEST_ONLY_FAILED");if(outcome==KMS_GATE_REFUSED)hold("GATE_NOT_RELEASED");
 if(outcome==KMS_APPLY_FAILED){printf("RATE_PARTIAL completed=%u\n",completed);hold("RATE_ERROR_STATE_UNCERTAIN");}
 /* Intervals between consecutive flip completions over the A/B phase only (k=2..steps). */
 uint64_t min=UINT64_MAX,max=0,sum=0;unsigned slow=0;
 for(unsigned k=2;k<=steps;k++){uint64_t d=t_done[k]-t_done[k-1];if(d<min)min=d;if(d>max)max=d;sum+=d;if(d>SLOW_US)slow++;}
 unsigned n=steps-1;uint64_t span=t_done[steps]-t_done[1];
 printf("RATE_SUMMARY steps=%u intervals=%u span_us=%" PRIu64 " mean_us=%" PRIu64 " min_us=%" PRIu64 " max_us=%" PRIu64 " slow=%u fps_x100=%" PRIu64 " first_us=%" PRIu64 "\n",
        steps,n,span,sum/n,min,max,slow,span?(uint64_t)n*100000000u/span:0,t_done[1]-t_start);
 for(unsigned k=1;k<=steps+1;k++)printf("T %u %u %" PRIu64 "\n",k,fb_at(k),t_done[k]-t_start);
 struct properties l=properties(95,DRM_MODE_OBJECT_PLANE),r=properties(128,DRM_MODE_OBJECT_PLANE);
 crtc=properties(205,DRM_MODE_OBJECT_CRTC);
 geometry(&l,CONFIRMED_FB,0,0);geometry(&r,CONFIRMED_FB,HALF,HALF);
 require(value(&crtc,"MODE_ID")==340 && value(&crtc,"ACTIVE")==1,"mode changed during flips");
 puts("RATE_RETURNED");hold("RATE_DONE_FB335_RETAINED");
}
