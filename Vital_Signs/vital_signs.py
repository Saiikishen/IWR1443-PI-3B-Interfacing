
import serial
import time
import math
import struct
import threading
import numpy as np
from collections import deque
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# ─── Configuration ────────────────────────────────────────────────────────────
CONFIG_FILE = '/home/iiot1/Radar/profile_2d_VitalSigns_20fps.cfg'
CLI_PORT    = '/dev/iwr_cli'
DATA_PORT   = '/dev/iwr_data'
BAUD_CLI    = 115200
BAUD_DATA   = 921600

# ─────────────────────────────────────────────────────────────────────────────
# Config parser — mirrors MATLAB parseCfg() exactly to get correct PKTLEN.
#
# ROOT CAUSE OF motionFlag=0:
#   PKTLEN was hardcoded as 288, assuming exactly 22 range bins processed.
#   The actual packet size depends on the vitalSignsCfg range window and
#   profileCfg parameters.  If it differs from 288 (e.g. 256 or 320), every
#   frame flushes the wrong number of bytes → buffer desyncs → byte 132 no
#   longer points at motionDetectedFlag; it reads range-profile noise which
#   is nearly always zero, so the flag appears permanently stuck at 0.
#
# Fix: parse the .cfg file and compute PKTLEN exactly as MATLAB does.
# ─────────────────────────────────────────────────────────────────────────────
def _pow2roundup(x):
    y = 1
    while x > y:
        y *= 2
    return y


def parse_cfg_for_pktlen(cfg_file):
    """
    Read the radar .cfg file and compute the correct packet length (bytes),
    replicating MATLAB parseCfg() exactly.
    Returns (pktlen_bytes, rangeBinSize_m).
    """
    LENGTH_HEADER_BYTES             = 40
    LENGTH_TLV_MESSAGE_HEADER_BYTES = 8
    LENGTH_DEBUG_DATA_OUT_BYTES     = 128
    MMWDEMO_OUTPUT_MSG_SEGMENT_LEN  = 32

    p = {}

    with open(cfg_file, 'r') as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith('%'):
                continue
            tok = line.split()
            if not tok:
                continue
            cmd = tok[0]

            if cmd == 'channelCfg':
                rx_en = int(tok[1])
                tx_en = int(tok[2])
                p['numTxAzimAnt'] = ((tx_en >> 0) & 1) + ((tx_en >> 2) & 1)
                p['numTxElevAnt'] = (tx_en >> 1) & 1
                p['numTxAnt']     = p['numTxAzimAnt'] + p['numTxElevAnt']

            elif cmd == 'profileCfg':
                # profileCfg <id> <startFreq> <idleTime> <adcStartTime>
                #             <rampEndTime> <txOutPower> <txPhaseShifter>
                #             <freqSlopeConst> <txStartTime> <numAdcSamples>
                #             <digOutSampleRate> ...
                p['startFreq']        = float(tok[2])   # GHz
                p['idleTime']         = float(tok[3])   # us
                p['rampEndTime']      = float(tok[5])   # us
                p['freqSlopeConst']   = float(tok[8])   # MHz/us
                p['numAdcSamples']    = int(tok[10])
                p['digOutSampleRate'] = float(tok[11])  # ksps

            elif cmd == 'frameCfg':
                p['chirpStartIdx'] = int(tok[1])
                p['chirpEndIdx']   = int(tok[2])
                p['numLoops']      = int(tok[3])
                p['numFrames']     = int(tok[4])

            elif cmd == 'vitalSignsCfg':
                p['rangeStartMeters'] = float(tok[1])
                p['rangeEndMeters']   = float(tok[2])

    # ── Derived quantities (exact MATLAB formulas from parseCfg lines 688-710) ─
    numRangeBins = _pow2roundup(p['numAdcSamples'])

    # MATLAB: freqSlopeConst_temp = 48*freqSlopeConst*2^26*1e3 / (3.6e9*900)
    freq_slope_temp    = (48.0 * p['freqSlopeConst'] * (2**26) * 1e3) / (3.6e9 * 900.0)
    chirp_duration_us  = 1e3 * p['numAdcSamples'] / p['digOutSampleRate']
    chirp_bw_kHz       = freq_slope_temp * chirp_duration_us
    range_max          = (chirp_duration_us * p['digOutSampleRate'] * 3e8) / \
                         (2.0 * chirp_bw_kHz * 1e9)
    range_bin_size_m   = range_max / numRangeBins

    range_start_idx    = math.floor(p['rangeStartMeters'] / range_bin_size_m)
    range_end_idx      = math.floor(p['rangeEndMeters']   / range_bin_size_m)
    num_bins_processed = range_end_idx - range_start_idx + 1

    # ── Packet size (MATLAB parseCfg lines 714-724) ───────────────────────────
    total  = LENGTH_HEADER_BYTES
    total += LENGTH_TLV_MESSAGE_HEADER_BYTES + 4 * num_bins_processed  # TLV1: range profile
    total += LENGTH_TLV_MESSAGE_HEADER_BYTES + LENGTH_DEBUG_DATA_OUT_BYTES  # TLV2: vital stats

    if total % MMWDEMO_OUTPUT_MSG_SEGMENT_LEN != 0:
        total = math.ceil(total / MMWDEMO_OUTPUT_MSG_SEGMENT_LEN) * MMWDEMO_OUTPUT_MSG_SEGMENT_LEN

    print(f"[cfg] rangeBinSize={range_bin_size_m*100:.2f} cm  "
          f"binsProcessed={num_bins_processed}  PKTLEN={total} bytes")

    return total, range_bin_size_m


# ─── Packet field byte offsets (0-based from packet start) ───────────────────
#
# Packet layout (PKTLEN bytes, padded to multiple of 32):
#   Bytes   0-39  : Frame header  (magic[8] + version[4] + packetLen[4] +
#                   platform[4] + frameNum[4] + cpuCycles[4] + numObj[4] +
#                   numTLVs[4] + subFrameNum[4])
#   Bytes  40-47  : TLV-1 header (type:u32 + length:u32)   ← vital stats
#   Bytes  48-175 : VitalSignsDemo_OutputStats (128 bytes) ← ALWAYS fixed here
#   Bytes 176-183 : TLV-2 header (type:u32 + length:u32)   ← range profile
#   Bytes 184+    : Range profile data (4 * numBinsProcessed bytes)
#   Tail          : Padding to multiple of 32
#
# Proof TLV order: INDEX_RANGE_PROFILE_START=35, TRANSLATE_INDEX(48,35)=47
#   → byte (47-1)*4 = 184 = 48+128+8 ✓   (vital stats first, range profile second)
#
# Vital stats is at a FIXED offset regardless of N, but PKTLEN changes with N.
# With the old hardcoded PKTLEN=288, any config producing N≠22 range bins would
# desync the buffer — which is why motionFlag (and others) were unreliable.

VITAL_STATS_OFFSET   = 48   # byte where VitalSignsDemo_OutputStats starts
RANGEBIN_BYTE_OFFSET = 50   # rangeBinIndexPhase uint16: MATLAB bytes 51:52 [1-based]
FRAME_NUM_BYTE_OFFSET = 20  # frameNumber uint32: MATLAB INDEX_GLOBAL_COUNT=21 [1-based]


def field_byte(index_1based):
    """
    0-based byte offset of a float32 field inside the vital-stats payload.
    Mirrors MATLAB TRANSLATE_INDEX(OFFSET=48, index) → (OFFSET+index*4)/4
    then converts back to byte: result = OFFSET + (index-1)*4
    """
    return VITAL_STATS_OFFSET + (index_1based - 1) * 4


# All float32 fields in VitalSignsDemo_OutputStats
B_RANGE_BIN_VALUE = field_byte(2)   # 52  — maxVal
B_PHASE_UNWRAP    = field_byte(5)   # 64  — unwrappedPhasePeak_mm
B_BREATH_WFM      = field_byte(6)   # 68  — outputFilterBreathOut
B_HEART_WFM       = field_byte(7)   # 72  — outputFilterHeartOut
B_HEART_FFT       = field_byte(8)   # 76  — heartRateEst_FFT    (BPM, not Hz)
B_HEART_FFT_4HZ   = field_byte(9)   # 80
B_HEART_XCORR     = field_byte(10)  # 84
B_HEART_PEAK      = field_byte(11)  # 88
B_BREATH_FFT      = field_byte(12)  # 92  — breathRateEst_FFT   (BPM, not Hz)
B_BREATH_XCORR    = field_byte(13)  # 96
B_BREATH_PEAK     = field_byte(14)  # 100
B_CM_BREATH       = field_byte(15)  # 104
B_CM_BREATH_XCORR = field_byte(16)  # 108
B_CM_HEART        = field_byte(17)  # 112
B_CM_HEART_4HZ    = field_byte(18)  # 116
B_CM_HEART_XCORR  = field_byte(19)  # 120
B_ENERGY_BREATH   = field_byte(20)  # 124
B_ENERGY_HEART    = field_byte(21)  # 128
B_MOTION_FLAG     = field_byte(22)  # 132 — motionDetectedFlag (float32: 0.0 or 1.0)

WAVEFORM_LEN = 150

app      = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

byteBuffer       = np.zeros(2**15, dtype='uint8')
byteBufferLength = 0

breathing_wave = deque([0.0] * WAVEFORM_LEN, maxlen=WAVEFORM_LEN)
heartbeat_wave = deque([0.0] * WAVEFORM_LEN, maxlen=WAVEFORM_LEN)

# ─────────────────────────────────────────────────────────────────────────────
# HTML Dashboard
# ─────────────────────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>IWR1443 Vital Signs</title>
  <script src="https://cdn.socket.io/4.6.0/socket.io.min.js"></script>
  <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#0a0f0a;color:#eee;font-family:monospace;display:flex;flex-direction:column;align-items:center}
    h2{margin:16px 0 4px;color:#00e5ff;letter-spacing:2px;font-size:20px}
    #status{font-size:13px;color:#666;margin-bottom:14px}
    .vitals{display:flex;gap:30px;margin-bottom:20px;flex-wrap:wrap;justify-content:center}
    .card{background:#0f1f0f;border:1px solid #1a3a1a;border-radius:10px;padding:16px 30px;text-align:center;min-width:160px}
    .card .label{font-size:11px;color:#888;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px}
    .card .value{font-size:46px;font-weight:bold;letter-spacing:2px}
    .card .unit{font-size:13px;color:#666;margin-top:4px}
    .card .sub{font-size:11px;color:#444;margin-top:6px}
    #br-val{color:#00ff88}#hr-val{color:#ff4d6d}
    #motion-card{background:#1a0f0f}
    .charts{display:flex;gap:16px;flex-wrap:wrap;justify-content:center}
    .chart-wrap{background:#0f1a0f;border:1px solid #1a3a1a;border-radius:8px;padding:10px}
    .chart-title{font-size:11px;color:#555;margin-bottom:6px;text-align:center;letter-spacing:1px}
    #guide{margin-top:14px;font-size:12px;color:#444;text-align:center;margin-bottom:16px}
  </style>
</head>
<body>
  <h2>&#x2665; IWR1443BOOST &mdash; Vital Signs Monitor</h2>
  <div id="status">Waiting for sensor data...</div>
  <div class="vitals">
    <div class="card">
      <div class="label">Breathing Rate</div>
      <div class="value" id="br-val">--</div>
      <div class="unit">breaths / min</div>
      <div class="sub" id="br-cm">CM: --</div>
    </div>
    <div class="card">
      <div class="label">Heart Rate</div>
      <div class="value" id="hr-val">--</div>
      <div class="unit">beats / min</div>
      <div class="sub" id="hr-cm">CM: --</div>
    </div>
    <div class="card">
      <div class="label">Range Bin</div>
      <div class="value" style="font-size:34px;color:#aaa" id="rng-val">--</div>
      <div class="unit">max energy bin</div>
      <div class="sub" id="energy-val">E_br:-- E_hr:--</div>
    </div>
    <div class="card" id="motion-card">
      <div class="label">Motion</div>
      <div class="value" style="font-size:34px;color:#ff4d6d" id="motion-val">--</div>
      <div class="unit">flag (1=large motion)</div>
    </div>
  </div>
  <div class="charts">
    <div class="chart-wrap">
      <div class="chart-title">BREATHING WAVEFORM (filter output)</div>
      <div id="br-chart" style="width:460px;height:190px"></div>
    </div>
    <div class="chart-wrap">
      <div class="chart-title">HEARTBEAT WAVEFORM (filter output)</div>
      <div id="hr-chart" style="width:460px;height:190px"></div>
    </div>
  </div>
  <div id="guide">Sit still &bull; Chest facing sensor &bull; 0.3&ndash;1.0m &bull; Wait ~30s for stable readings</div>
  <script>
    var N=150,xs=Array.from({length:N},(_,i)=>i);
    var bL={paper_bgcolor:'#0f1a0f',plot_bgcolor:'#0a120a',margin:{t:10,b:28,l:38,r:8},
            xaxis:{color:'#333',showgrid:false,zeroline:false},
            yaxis:{color:'#444',gridcolor:'#1a2a1a',zeroline:true,zerolinecolor:'#333'}};
    var hL=JSON.parse(JSON.stringify(bL));
    hL.paper_bgcolor='#1a0a0f';hL.plot_bgcolor='#120a0a';
    Plotly.newPlot('br-chart',[{x:xs,y:Array(N).fill(0),mode:'lines',
      line:{color:'#00ff88',width:2},fill:'tozeroy',fillcolor:'rgba(0,255,136,0.06)'}],
      bL,{displayModeBar:false});
    Plotly.newPlot('hr-chart',[{x:xs,y:Array(N).fill(0),mode:'lines',
      line:{color:'#ff4d6d',width:2},fill:'tozeroy',fillcolor:'rgba(255,77,109,0.06)'}],
      hL,{displayModeBar:false});
    var socket=io();
    socket.on('vital_signs',function(d){
      document.getElementById('status').innerText='Frame #'+d.frame+'  |  Sensor active';
      if(d.br_bpm>0){document.getElementById('br-val').innerText=d.br_bpm.toFixed(1);
                     document.getElementById('br-cm').innerText='CM: '+d.cm_breath.toFixed(3);}
      if(d.hr_bpm>0){document.getElementById('hr-val').innerText=d.hr_bpm.toFixed(1);
                     document.getElementById('hr-cm').innerText='CM: '+d.cm_heart.toFixed(3);}
      document.getElementById('rng-val').innerText=d.rangeBin;
      document.getElementById('energy-val').innerText='E_br:'+d.energy_breath.toFixed(1)+' E_hr:'+d.energy_heart.toFixed(2);
      /* FIX: firmware sends float 1.0; use > 0.5 rather than == 1
         to be robust against any minor floating-point rounding */
      var motionOn = d.motion > 0.5;
      document.getElementById('motion-val').innerText = motionOn ? 'YES' : 'NO';
      document.getElementById('motion-card').style.background = motionOn ? '#2a0a0a' : '#0f1a0f';
      Plotly.restyle('br-chart',{y:[d.br_wave]},[0]);
      Plotly.restyle('hr-chart',{y:[d.hr_wave]},[0]);
    });
  </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML)


# ─────────────────────────────────────────────────────────────────────────────
# Serial config
# ─────────────────────────────────────────────────────────────────────────────
def read_response(port, timeout=2.0):
    buf = ''
    start = time.time()
    while time.time() - start < timeout:
        if port.in_waiting:
            buf += port.read(port.in_waiting).decode(errors='ignore')
            if 'Done' in buf or 'Error' in buf or 'not recognized' in buf:
                break
        time.sleep(0.05)
    return buf.strip()


def serialConfig(configFileName):
    print(f"[*] Opening CLI  port : {CLI_PORT}")
    print(f"[*] Opening Data port : {DATA_PORT}\n")
    CLIport  = serial.Serial(CLI_PORT,  BAUD_CLI,  timeout=1)
    Dataport = serial.Serial(DATA_PORT, BAUD_DATA, timeout=1)
    time.sleep(3)
    CLIport.reset_input_buffer()
    Dataport.reset_input_buffer()
    for line in open(configFileName):
        line = line.strip()
        if not line or line.startswith('%'):
            continue
        CLIport.write((line + '\r\n').encode())
        resp = read_response(CLIport)
        print(f"  [{'✓' if 'Done' in resp else '✗'}] {line}")
    print("\n[✓] Sensor RUNNING\n")
    return CLIport, Dataport


# ─────────────────────────────────────────────────────────────────────────────
# NaN / Inf guard — mirrors MATLAB lines 384-395
# ─────────────────────────────────────────────────────────────────────────────
def safe_float(v, fallback=0.0):
    return fallback if (math.isnan(v) or math.isinf(v)) else v


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────
def readAndParseVitalSigns(Dataport, PKTLEN):
    global byteBuffer, byteBufferLength

    MAGIC   = [2, 1, 4, 3, 6, 5, 8, 7]
    MAX_BUF = 2**15

    out = {
        'breathRate_bpm': 0.0, 'heartRate_bpm': 0.0,
        'cm_breath': 0.0,      'cm_heart': 0.0,
        'energy_breath': 0.0,  'energy_heart': 0.0,
        'filterBreath': 0.0,   'filterHeart': 0.0,
        'rangeBin': 0,         'motionFlag': 0.0,
        'hasData': False
    }

    # Fill buffer — read all available bytes, block only if empty
    avail = Dataport.in_waiting
    raw   = Dataport.read(avail) if avail > 0 else Dataport.read(1)
    vec   = np.frombuffer(raw, dtype='uint8')
    n     = len(vec)
    if n > 0 and byteBufferLength + n < MAX_BUF:
        byteBuffer[byteBufferLength:byteBufferLength + n] = vec
        byteBufferLength += n

    if byteBufferLength < PKTLEN:
        return 0, 0, out

    # Locate magic word
    locs   = np.where(byteBuffer[:byteBufferLength] == MAGIC[0])[0]
    starts = [l for l in locs
              if l + 8 <= byteBufferLength and
              np.all(byteBuffer[l:l + 8] == MAGIC)]

    if not starts:
        keep = min(7, byteBufferLength)
        byteBuffer[:keep] = byteBuffer[byteBufferLength - keep:byteBufferLength]
        byteBufferLength  = keep
        return 0, 0, out

    s = starts[0]
    if s > 0:
        byteBuffer[:byteBufferLength - s] = byteBuffer[s:byteBufferLength]
        byteBuffer[byteBufferLength - s:] = 0
        byteBufferLength -= s

    if byteBufferLength < PKTLEN:
        return 0, 0, out

    pkt = bytes(byteBuffer[:PKTLEN])

    frameNumber = struct.unpack_from('<I', pkt, FRAME_NUM_BYTE_OFFSET)[0]
    rangeBin    = struct.unpack_from('<H', pkt, RANGEBIN_BYTE_OFFSET)[0]

    def f32(offset):
        return struct.unpack_from('<f', pkt, offset)[0] if offset + 4 <= PKTLEN else 0.0

    out['rangeBin']       = int(rangeBin)
    out['filterBreath']   = safe_float(f32(B_BREATH_WFM))
    out['filterHeart']    = safe_float(f32(B_HEART_WFM))
    out['heartRate_bpm']  = safe_float(f32(B_HEART_FFT))   # already BPM
    out['breathRate_bpm'] = safe_float(f32(B_BREATH_FFT))  # already BPM
    out['cm_breath']      = safe_float(f32(B_CM_BREATH))
    out['cm_heart']       = safe_float(f32(B_CM_HEART))
    out['energy_breath']  = safe_float(f32(B_ENERGY_BREATH))
    out['energy_heart']   = safe_float(f32(B_ENERGY_HEART))
    # motionDetectedFlag is float32 in firmware: 0.0=still, 1.0=motion
    # With correct PKTLEN the buffer stays in sync so byte 132 is always right.
    out['motionFlag']     = safe_float(f32(B_MOTION_FLAG))
    out['hasData']        = True

    # Flush exactly one packet — this is what kept the buffer in sync in MATLAB
    byteBuffer[:byteBufferLength - PKTLEN] = byteBuffer[PKTLEN:byteBufferLength]
    byteBuffer[byteBufferLength - PKTLEN:] = 0
    byteBufferLength -= PKTLEN

    return 1, frameNumber, out


# ─────────────────────────────────────────────────────────────────────────────
# Radar thread
# ─────────────────────────────────────────────────────────────────────────────
def radar_thread(Dataport, PKTLEN):
    global breathing_wave, heartbeat_wave
    print("[*] Vital signs thread started\n")
    while True:
        try:
            dataOK, frameNumber, out = readAndParseVitalSigns(Dataport, PKTLEN)
            if dataOK and out['hasData']:
                breathing_wave.append(float(out['filterBreath']))
                heartbeat_wave.append(float(out['filterHeart']))

                motion_str = "MOTION" if out['motionFlag'] > 0.5 else "still"
                print(f"Frame #{frameNumber:05d} | "
                      f"Breath: {out['breathRate_bpm']:.1f} bpm  "
                      f"Heart: {out['heartRate_bpm']:.1f} bpm  "
                      f"CM_b={out['cm_breath']:.2f} CM_h={out['cm_heart']:.2f}  "
                      f"motion={out['motionFlag']:.1f} ({motion_str})")

                socketio.emit('vital_signs', {
                    'frame'        : int(frameNumber),
                    'br_bpm'       : round(out['breathRate_bpm'],  1),
                    'hr_bpm'       : round(out['heartRate_bpm'],   1),
                    'cm_breath'    : round(out['cm_breath'], 3),
                    'cm_heart'     : round(out['cm_heart'],  3),
                    'energy_breath': round(out['energy_breath'], 2),
                    'energy_heart' : round(out['energy_heart'],  3),
                    'rangeBin'     : out['rangeBin'],
                    'motion'       : out['motionFlag'],  # raw float; JS checks > 0.5
                    'br_wave'      : list(breathing_wave),
                    'hr_wave'      : list(heartbeat_wave)
                })
            elif not dataOK:
                time.sleep(0.01)
        except Exception as e:
            print(f"[!] Error in radar_thread: {e}")
            time.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Compute correct packet length from config BEFORE opening the sensor.
    # This is the key fix — PKTLEN is now dynamic, not hardcoded.
    PKTLEN, _ = parse_cfg_for_pktlen(CONFIG_FILE)

    CLIport, Dataport = serialConfig(CONFIG_FILE)

    t = threading.Thread(target=radar_thread, args=(Dataport, PKTLEN), daemon=True)
    t.start()

    print("[*] Open http://<raspberry-pi-ip>:5000 in your browser\n")
    try:
        socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n[*] Stopping...")
        CLIport.write(b'sensorStop\r\n')
        CLIport.close()
        Dataport.close()
        print("[✓] Done.")
