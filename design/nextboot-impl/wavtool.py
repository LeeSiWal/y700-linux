"""WAV handling for the Y700 speaker path (48 kHz / 16-bit / stereo into the WR_SHARED_MEM_EP).
- read_wav(): PCM WAV 8/16/24/32-bit, mono or stereo, any rate -> s16 stereo 48 kHz (linear resampling when needed)
- limit(): scales the whole clip down so its peak is <= limit_dbfs (no speaker protection runs on this path)
- tone helpers for the channel test; make_melody(): a short deterministic test clip."""
import math, struct, wave
from array import array

RATE = 48000

def read_wav(path):
    w = wave.open(str(path), 'rb')
    ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n); w.close()
    if ch not in (1, 2): raise ValueError('only mono/stereo WAV is supported (got %d channels)' % ch)
    if sw == 1: s = [(b - 128) << 8 for b in raw]
    elif sw == 2: s = array('h', raw).tolist()
    elif sw == 3: s = [int.from_bytes(raw[i:i + 3], 'little', signed=True) >> 8 for i in range(0, len(raw), 3)]
    elif sw == 4: s = [v >> 16 for v in array('i', raw)]
    else: raise ValueError('unsupported sample width %d' % sw)
    frames = [(s[i], s[i + 1]) for i in range(0, len(s), 2)] if ch == 2 else [(v, v) for v in s]
    if rate != RATE:
        out, step = [], rate / RATE
        for j in range(int(len(frames) / step)):
            x = j * step; i = int(x); f = x - i; a = frames[i]; b = frames[min(i + 1, len(frames) - 1)]
            out.append((int(a[0] + (b[0] - a[0]) * f), int(a[1] + (b[1] - a[1]) * f)))
        frames = out
    info = {'channels': ch, 'sample_width': sw, 'rate': rate, 'frames': n, 'resampled': rate != RATE}
    return frames, info

def limit(frames, limit_dbfs=-12.0):
    peak = max((max(abs(l), abs(r)) for l, r in frames), default=0)
    cap = int(32767 * 10 ** (limit_dbfs / 20)); g = min(1.0, cap / peak) if peak else 1.0
    return [(int(l * g), int(r * g)) for l, r in frames], {'peak_in': peak, 'gain': round(g, 4), 'cap': cap}

def to_bytes(frames):
    a = array('h')
    for l, r in frames: a.append(l); a.append(r)
    return a.tobytes()

def pulses(freq, n, on, off, dbfs=-18.0, left=True, right=True):
    amp = 32767 * 10 ** (dbfs / 20); ramp = int(0.005 * RATE); frames = []
    for _ in range(n):
        m = int(on * RATE)
        for i in range(m):
            v = int(amp * min(1.0, i / ramp, (m - 1 - i) / ramp) * math.sin(2 * math.pi * freq * i / RATE))
            frames.append((v if left else 0, v if right else 0))
        frames += [(0, 0)] * int(off * RATE)
    return frames

def make_melody(path, dbfs=-18.0):
    """C major scale up and down (0.25 s notes, 30 ms fades) as a 16-bit 48 kHz stereo WAV."""
    notes = [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25]
    amp = 32767 * 10 ** (dbfs / 20); n = int(0.25 * RATE); fade = int(0.03 * RATE); a = array('h')
    for f in notes + notes[::-1]:
        for i in range(n):
            v = int(amp * min(1.0, i / fade, (n - 1 - i) / fade) * math.sin(2 * math.pi * f * i / RATE)); a.append(v); a.append(v)
    w = wave.open(str(path), 'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(a.tobytes()); w.close()

if __name__ == '__main__':
    import sys
    make_melody(sys.argv[1] if len(sys.argv) > 1 else 'melody.wav')
