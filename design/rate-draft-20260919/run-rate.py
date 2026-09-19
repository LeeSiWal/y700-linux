#!/usr/bin/env python3
"""Back-to-back A/B flip rate on retained FB341/342, then back to FB335. One-shot; no retry."""
from pathlib import Path
import fcntl, hashlib, json, os, re, signal, subprocess, sys, time
import health_guard as h
from borrow import verify_original, borrow_fd
from ratelog import RateLog

B=Path(__file__).resolve().parent
c=h.c;need=h.need
interrupted=False
UNDERRUN=re.compile(r'intf:(\d+)\s+vsync:\s+\d+\s+underrun:\s+(\d+)\s+mode:\s+video')

def save(name,value):
    with (B/name).open('x') as f:
        json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.chmod(B/name,0o644)

def underruns(text):
    found={int(i):int(n) for i,n in UNDERRUN.findall(text)}
    need(set(found)=={1,2},'unexpected encoder counter format')
    return found

def check_state(label):
    # Initial, after-TEST_ONLY and final states must all equal the user-confirmed native FB335 state.
    need(c.read('/sys/kernel/debug/dri/0/state')==c.read(B/'expected-state.txt'),'DRM state differs at '+label)

def check_clients():
    lines=c.read('/sys/kernel/debug/dri/0/clients').splitlines()
    need(len(lines)==2 and lines[0].split()==['command','tgid','dev','master','a','uid','magic']
         and lines[1].split()==['kms-held','2604','0','y','y','0','0'],'DRM ownership changed')

def check_retained(m):
    for w in m['retained_workers']:
        p=Path('/proc')/str(w['pid'])
        need(p.exists(),'retained worker gone: '+str(w['pid']))
        need(c.task_stat(c.read(p/'stat'))['starttime']==w['starttime'],'retained worker identity changed: '+str(w['pid']))

def worker_identity(child,identity,m):
    verify_original(m,c);check_retained(m)
    need(child.poll() is None,'worker exited; resources may have been released; no retry')
    p=Path('/proc')/str(child.pid)
    need(c.task_stat(c.read(p/'stat'))['starttime']==identity['starttime'],'worker PID reused')
    need((p/'exe').resolve()==B/'rate-held','worker executable changed')
    need(hashlib.sha256(c.read(p/'exe',True)).hexdigest()==m['files']['rate-held'],'worker binary changed')

def snapshot(label):
    result={'phase':label,'uptime':c.read('/proc/uptime').strip(),'clocks':c.clocks(),
            'state':c.read('/sys/kernel/debug/dri/0/state'),
            'clients':c.read('/sys/kernel/debug/dri/0/clients'),
            'output_counters':c.optional('/sys/kernel/debug/dri/0/encoder68/status'),
            'crtc_perf':c.optional('/sys/kernel/debug/dri/0/crtc205/state')}
    save('snapshot-'+label+'.json',result)
    return result

def static(m):
    for name,digest in m['files'].items():
        need(Path(name).name==name and hashlib.sha256(c.read(B/name,True)).hexdigest()==digest,'bundle changed: '+name)
    monitor=h.Monitor(m);monitor.context();monitor.check_log(c.dmesg())
    need(not (B/'attempt.json').exists(),'already attempted; inspect result, do not retry')
    need(Path('/sys/class/drm/card0/device/driver').resolve().name=='msm_drm','wrong display driver')
    need(c.read('/sys/class/drm/card0-DSI-1/status').strip()=='connected','panel disconnected')
    need(c.read('/sys/class/drm/card0-DSI-1/modes').splitlines()[0]=='1904x3040x120vid','native mode changed')
    need(Path('/sys/bus/platform/drivers/ramoops/ramoops').is_symlink(),'ramoops not bound')
    for name in m['module_notes']:
        need(c.read(Path('/sys/module')/name/'initstate').strip()=='live','module not live: '+name)
    check_retained(m)
    return monitor

def stop_worker(child,report,reason):
    # SIGINT is the worker's designed stop: it ends further flips and holds every resource.
    report.setdefault('stop_requests',[]).append({'time':time.time(),'reason':reason})
    try:os.kill(child.pid,signal.SIGINT)
    except ProcessLookupError:pass

def supervise(child,identity,m,monitor,report,base):
    steps=m['rate_review']['steps'];log=RateLog(steps)
    start=time.monotonic();ready=None;go=None;returned=None;offset=0;pending=b''
    try:
        while True:
            now=time.monotonic();monitor.tick()
            need(not interrupted,'interrupted; resources retained')
            worker_identity(child,identity,m)
            counts=underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status'))
            if go is not None:report['underrun_samples'].append({'t':round(now-go,3),'counts':counts})
            if counts!=base:
                if go is not None and returned is None:stop_worker(child,report,'underrun counter changed')
                need(False,'underrun counter changed: '+str(base)+' -> '+str(counts))
            raw=(B/'worker.log').read_bytes();pending+=raw[offset:];offset=len(raw)
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1);line=line.decode(errors='replace')
                if not line.startswith('T '):print(line,flush=True)
                event=log.feed(line)
                if event=='ready':ready=now
                if event=='returned':
                    returned=now;report['rate_returned']=True;report['summary']=log.summary;report['times_us']=log.times
                    report['snapshots'].append(snapshot('immediately-after-rate'))
                    check_state('after-rate')
            if ready is not None and go is None and now-ready>=2:
                monitor.observe(2,quiet=True)
                need(not interrupted,'interrupted before gate')
                worker_identity(child,identity,m);check_clients();check_state('after-test')
                _,unknown=monitor.tick();need(not unknown,'unreviewed task immediately before gate')
                need(underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status'))==base,'underrun changed before gate')
                report['snapshots'].append(snapshot('before-rate'))
                save('commit-intent.json',{'boot_id':m['boot_id'],'worker':identity,'steps':steps,'time':time.time()})
                # Marked released before the write: an uncertain write never permits a retry.
                go=now;log.go_sent=True;report['gate_released']=True
                print('RATE_GATE_RELEASED steps=%d'%steps,flush=True)
                child.stdin.write(b'GO\n');child.stdin.flush()
            if go is not None and returned is None and now-go>=steps*0.02+10:
                stop_worker(child,report,'rate deadline')
                need(False,'rate loop did not finish in time; worker asked to hold')
            if returned is not None and now-returned>=30:
                monitor.observe(2,quiet=True);worker_identity(child,identity,m);check_clients();check_state('after-30-seconds')
                need(underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status'))==base,'underrun changed after rate')
                report['snapshots'].append(snapshot('after-30-seconds'))
                s=log.summary
                print('RATE_RESULT fps=%.2f mean_ms=%.3f min_ms=%.3f max_ms=%.3f slow(>12.5ms)=%d/%d'%(
                    s['fps_x100']/100,s['mean_us']/1000,s['min_us']/1000,s['max_us']/1000,s['slow'],s['intervals']),flush=True)
                report['status']='RATE_DONE_FB335_HELD_AWAITING_VISUAL_CONFIRMATION'
                print(report['status'],flush=True)
                return
            need(now-start<steps*0.02+120,'rate trial timeout; resources retained, no retry')
            time.sleep(.25)
    finally:
        try:child.stdin.close()  # EOF denies an unsent GO.
        except BrokenPipeError:pass

def main():
    need(sys.argv[1:] in (['--check'],['--show']),'use --check or --show')
    m=json.loads(c.read(B/'manifest.json'));monitor=static(m)
    if sys.argv[1]=='--check':print('STATIC PREFLIGHT PASS: same boot, bundle hashes, modules, retained workers. Root DRM state checks run on --show.');return
    need(os.geteuid()==0,'interactive sudo required')
    with (B/'rate.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        monitor.observe(2,quiet=True);verify_original(m,c);check_state('initial');check_clients()
        need(c.read('/sys/kernel/debug/clk/disp_cc_mdss_mdp_clk_src/clk_rate').strip()=='85714334','current software clock request changed')
        base=underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status'))
        need(base=={int(k):v for k,v in m['rate_review']['underrun_base'].items()},'underrun base differs from review')
        save('attempt.json',{'boot_id':m['boot_id'],'time':time.time()})
        report={'boot_id':m['boot_id'],'gate_released':False,'rate_returned':False,'underrun_samples':[],'steps':m['rate_review']['steps'],
                'underrun_base':base,'dmesg_before':c.dmesg(),'snapshots':[snapshot('before-worker')],
                'physical_output':'requires user observation; events/readback/timing are not proof of visible frames'}
        try:
            borrowed=borrow_fd(m,c)
            try:
                with (B/'worker.log').open('xb',buffering=0) as out:
                    child=subprocess.Popen([str(B/'rate-held'),'--inherited-fd',str(borrowed),'--steps',str(m['rate_review']['steps'])],pass_fds=(borrowed,),stdin=subprocess.PIPE,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            finally:
                os.close(borrowed)  # Duplicate only; PID2604 and every retained worker keep the file.
            os.chmod(B/'worker.log',0o644)
            identity={'pid':child.pid,'starttime':c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'],
                      'boot_id':m['boot_id'],'binary_sha256':m['files']['rate-held']}
            report['worker']=identity;save('worker.json',identity)
            print('PREPARING',flush=True)
            supervise(child,identity,m,monitor,report,base)
        except BaseException as exc:
            report['error']=str(exc);report['status']='STOPPED_INSPECT_RETAINED_RESOURCES';raise
        finally:
            for key,fn in [('dmesg_after',c.dmesg),('tasks_after',c.tasks),('battery_after',c.battery)]:
                try:report[key]=fn()
                except Exception as exc:report[key+'_error']=str(exc)
            save('result.json',report);print('Saved:',B/'result.json',flush=True)

if __name__=='__main__':
    def stop(*_):
        global interrupted;interrupted=True
    for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    signal.signal(signal.SIGALRM,c.timeout)
    try:main()
    except Exception as exc:sys.exit('STOP: '+str(exc))
