/* DRAFT, UNBUILT, UNRUN. Finite two-plane flip test on the inherited DRM file.
 * Two new cached 1904x3040 buffers (A,B) are filled and CPU-verified before any
 * commit and never written again. Each step is one blocking atomic commit that
 * changes only FB_ID on plane95 and plane128 together, with PAGE_FLIP_EVENT for
 * a timestamp. The last step returns both planes to the user-confirmed FB335.
 * Any error/timeout holds every resource: no close, no restore, no retry. */
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <drm/drm_fourcc.h>
#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
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
#define STEPS 8          /* A,B alternations; small finite count first */
#define STEP_MS 500      /* slow enough to observe by eye; not a rate test */
#define EVENT_TIMEOUT_MS 1000
static int fd=-1;
static volatile sig_atomic_t stop;
static void interrupted(int sig){(void)sig;stop=1;}
static void hold(const char *reason) __attribute__((noreturn));
static void hold(const char *reason){
 printf("HELD pid=%d reason=%s\n",getpid(),reason);fflush(stdout);
 for(;;)sleep(1); /* FD, buffers and framebuffers deliberately remain owned. */
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
/* Only FB_ID of both planes changes; one atomic transaction keeps the halves in step. */
struct flip {uint32_t objects[2],counts[2],ids[2];uint64_t values[2];};
static struct flip flip_to(uint32_t fb,uint32_t left_fb_prop,uint32_t right_fb_prop){
 struct flip f={.objects={95,128},.counts={1,1},.ids={left_fb_prop,right_fb_prop},.values={fb,fb}};return f;
}
static int commit(struct flip *f,uint32_t flags,uint64_t user){
 struct drm_mode_atomic a={.flags=flags,.count_objs=2,.objs_ptr=(uintptr_t)f->objects,.count_props_ptr=(uintptr_t)f->counts,.props_ptr=(uintptr_t)f->ids,.prop_values_ptr=(uintptr_t)f->values,.user_data=user};
 return ioctl(fd,DRM_IOCTL_MODE_ATOMIC,&a);
}
/* Consume exactly one FLIP_COMPLETE for this step. Only this worker reads the
 * shared DRM file; retained workers sleep in hold() and never read(). */
static struct drm_event_vblank wait_event(uint64_t user){
 struct pollfd pfd={.fd=fd,.events=POLLIN};int rc=poll(&pfd,1,EVENT_TIMEOUT_MS);
 require(rc==1 && (pfd.revents&POLLIN),"flip event timeout; state uncertain");
 struct drm_event_vblank ev;ssize_t got=read(fd,&ev,sizeof(ev));
 require(got==(ssize_t)sizeof(ev) && ev.base.type==DRM_EVENT_FLIP_COMPLETE && ev.base.length==sizeof(ev),"unexpected DRM event");
 require(ev.user_data==user && ev.crtc_id==205,"event for another commit/CRTC");
 return ev;
}
static uint32_t pattern(unsigned which,unsigned x,unsigned y){
 /* A: bars left->right; B: same bars reversed, plus a distinct centre block. */
 static const uint32_t colors[]={0xffffff,0xffff00,0x00ffff,0x00ff00,0xff00ff,0xff0000,0x0000ff,0x404040};
 unsigned bar=(x*8)/W;uint32_t c=colors[which?7-bar:bar];
 if(y>H*3/4)c=((x/96+y/96+which)&1)?0xffffff:0x101010;
 if(which && x>W/2-200 && x<W/2+200 && y>H/2-200 && y<H/2+200)c=0x000000;
 if(x<12||y<12||x>W-13||y>H-13)c=0xffffff;
 return c;
}
static uint32_t make_fb(unsigned which,uint32_t *handle){
 struct drm_mode_create_dumb dumb={.height=H,.width=W,.bpp=32};checked(DRM_IOCTL_MODE_CREATE_DUMB,&dumb,"create dumb buffer");
 require(dumb.pitch==7680 && dumb.size==23347200,"cached buffer differs from reviewed geometry");
 struct drm_mode_map_dumb map={.handle=dumb.handle};checked(DRM_IOCTL_MODE_MAP_DUMB,&map,"map buffer offset");
 void *pixels=mmap(NULL,dumb.size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,(off_t)map.offset);require(pixels!=MAP_FAILED,"mmap failed");
 for(unsigned y=0;y<H;y++){uint32_t *row=(uint32_t*)((char*)pixels+(size_t)y*dumb.pitch);for(unsigned x=0;x<W;x++)row[x]=pattern(which,x,y);}
 __sync_synchronize(); /* ordering only; same cached dumb path that FB335 used */
 size_t n=0;
 for(unsigned y=0;y<H;y++){volatile uint32_t *row=(volatile uint32_t*)((char*)pixels+(size_t)y*dumb.pitch);for(unsigned x=0;x<W;x++){require(row[x]==pattern(which,x,y),"CPU buffer readback mismatch");n++;}}
 /* Mapping kept; buffer is never written after this point. */
 struct drm_mode_fb_cmd2 fb={.width=W,.height=H,.pixel_format=DRM_FORMAT_XRGB8888};fb.handles[0]=dumb.handle;fb.pitches[0]=dumb.pitch;checked(DRM_IOCTL_MODE_ADDFB2,&fb,"add XRGB framebuffer");
 struct drm_mode_fb_cmd2 actual={.fb_id=fb.fb_id};checked(DRM_IOCTL_MODE_GETFB2,&actual,"verify framebuffer");
 require(actual.width==W && actual.height==H && actual.pitches[0]==dumb.pitch && actual.pixel_format==DRM_FORMAT_XRGB8888 && actual.modifier[0]==0 && actual.offsets[0]==0,"framebuffer layout mismatch");
 require(fb.fb_id!=0 && fb.fb_id!=78 && fb.fb_id!=CONFIRMED_FB,"new framebuffer identity invalid");
 printf("BUFFER %c fb=%u handle=%u readback_pixels=%zu\n",which?'B':'A',fb.fb_id,dumb.handle,n);*handle=dumb.handle;return fb.fb_id;
}
static uint32_t fb_ids[2],left_prop,right_prop;
static int phase_test(void *ctx){(void)ctx;if(stop)return -1;
 for(unsigned i=0;i<2;i++){struct flip f=flip_to(fb_ids[i],left_prop,right_prop);if(commit(&f,DRM_MODE_ATOMIC_TEST_ONLY,0)<0){perror("ATOMIC TEST_ONLY");return -1;}}
 struct flip back=flip_to(CONFIRMED_FB,left_prop,right_prop);if(commit(&back,DRM_MODE_ATOMIC_TEST_ONLY,0)<0){perror("ATOMIC TEST_ONLY return");return -1;}
 return 0;}
static bool phase_gate(void *ctx){(void)ctx;char gate[16]={0};puts("TEST_ONLY_PASS");puts("READY_FOR_FLIPS");if(!fgets(gate,sizeof(gate),stdin))return false;return kms_gate_matches(gate,stop!=0);}
static double now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e3+t.tv_nsec/1e6;}
static int step(uint32_t fb,uint64_t user,const char *label){
 if(stop)return -1;struct flip f=flip_to(fb,left_prop,right_prop);double t0=now_ms();
 printf("FLIP_ENTER %s fb=%u user=%" PRIu64 "\n",label,fb,user);
 if(commit(&f,DRM_MODE_PAGE_FLIP_EVENT,user)<0){perror("ATOMIC flip");return -1;}
 struct drm_event_vblank ev=wait_event(user);
 printf("FLIP_DONE %s fb=%u seq=%u ts=%u.%06u ioctl_to_event_ms=%.2f\n",label,fb,ev.sequence,ev.tv_sec,ev.tv_usec,now_ms()-t0);
 struct properties l=properties(95,DRM_MODE_OBJECT_PLANE),r=properties(128,DRM_MODE_OBJECT_PLANE);
 geometry(&l,fb,0,0);geometry(&r,fb,HALF,HALF);return 0;
}
static int phase_apply(void *ctx){(void)ctx;
 for(unsigned k=0;k<STEPS;k++){if(step(fb_ids[k&1],k+1,(k&1)?"B":"A")<0)return -1;struct timespec d={.tv_sec=STEP_MS/1000,.tv_nsec=(STEP_MS%1000)*1000000L};nanosleep(&d,NULL);}
 return step(CONFIRMED_FB,STEPS+1,"RETURN_FB335");
}
int main(int argc,char **argv){
 require(argc==3 && !strcmp(argv[1],"--inherited-fd"),"requires inherited DRM descriptor");
 char *end=NULL;long inherited=strtol(argv[2],&end,10);require(end && !*end && inherited>=3 && inherited<=1024,"invalid descriptor");fd=(int)inherited;
 setbuf(stdout,NULL);signal(SIGINT,interrupted);signal(SIGTERM,interrupted);signal(SIGHUP,interrupted);signal(SIGPIPE,SIG_IGN);
 struct stat st;require(fstat(fd,&st)==0 && S_ISCHR(st.st_mode) && major(st.st_rdev)==226 && minor(st.st_rdev)==0,"wrong card0 descriptor");
 puts("INHERITED_DRM_FILE all retained workers and FB78/FB335 kept; no new DRM open");
 struct properties left=properties(95,DRM_MODE_OBJECT_PLANE),right=properties(128,DRM_MODE_OBJECT_PLANE),crtc=properties(205,DRM_MODE_OBJECT_CRTC),conn=properties(69,DRM_MODE_OBJECT_CONNECTOR);
 require(value(&conn,"CRTC_ID")==205 && value(&crtc,"ACTIVE")==1 && value(&crtc,"MODE_ID")==340,"native display state changed");
 geometry(&left,CONFIRMED_FB,0,0);geometry(&right,CONFIRMED_FB,HALF,HALF);
 require(value(&left,"zpos")==0 && value(&right,"zpos")==1,"plane ordering changed");
 left_prop=left.id[indexof(&left,"FB_ID")];right_prop=right.id[indexof(&right,"FB_ID")];
 uint32_t ha,hb;fb_ids[0]=make_fb(0,&ha);fb_ids[1]=make_fb(1,&hb);
 printf("PLAN steps=%d interval_ms=%d blocking atomic FB_ID-only on planes95+128; final return to FB%d\n",STEPS,STEP_MS,CONFIRMED_FB);
 struct kms_actions actions={.ctx=NULL,.test=phase_test,.gate=phase_gate,.apply=phase_apply};enum kms_outcome outcome=kms_once(&actions);
 if(outcome==KMS_TEST_FAILED)hold("TEST_ONLY_FAILED");if(outcome==KMS_GATE_REFUSED)hold("GATE_NOT_RELEASED");if(outcome==KMS_APPLY_FAILED)hold("FLIP_ERROR_STATE_UNCERTAIN");
 crtc=properties(205,DRM_MODE_OBJECT_CRTC);require(value(&crtc,"MODE_ID")==340 && value(&crtc,"ACTIVE")==1,"mode changed during flips");
 puts("FLIPS_RETURNED");hold("FLIP_BUFFERS_RETAINED_VISUAL_CONFIRMATION_REQUIRED");
}
