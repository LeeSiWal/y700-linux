"""audio-d5: left/right check + WAV playback on the speaker path (SD1, 32-bit slots).
  L: LEFT channel only, ONE long beep (3 s)     -> which physical speaker?
  R: RIGHT channel only, THREE short beeps      -> which physical speaker?
  W: melody.wav (C major scale up/down, 4 s) through wavtool.read_wav + limit(-12 dBFS)
Each clip is a full open/play/close (audiod4.run); 6 s pauses between clips."""
import time
import audiod4, wavtool as w

def clips(bundle):
    frames, info = w.read_wav(bundle/'melody.wav'); frames, lim = w.limit(frames, -12.0)
    return [('L', 'LEFT channel only - ONE long beep', w.to_bytes(w.pulses(660, 1, 3.0, 0.5, left=True, right=False)), {}),
            ('R', 'RIGHT channel only - THREE short beeps', w.to_bytes(w.pulses(660, 3, 0.4, 0.6, left=False, right=True)), {}),
            ('W', 'WAV melody.wav - scale up and down', w.to_bytes(frames), {'wav': info, 'limit': lim})]

def run_all(mon, report, bundle):
    report['segments'] = []
    for name, desc, pcm, meta in clips(bundle):
        print('SEGMENT %s: %s' % (name, desc), flush=True)
        sub = {}
        try: audiod4.run(mon, sub, pcm=pcm, label=name)
        finally: report['segments'].append({'name': name, 'desc': desc, **meta, **sub.get('play', {})})
        print('SEGMENT %s finished' % name, flush=True); time.sleep(6)
    return report['segments']
