import serial
import time
import numpy as np
import threading
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# ─── Configuration ────────────────────────────────────────────────────────────
CONFIG_FILE = '/home/iiot1/Radar/iwr1443_config.cfg'
CLI_PORT    = '/dev/iwr_cli'
DATA_PORT   = '/dev/iwr_data'
BAUD_CLI    = 115200
BAUD_DATA   = 921600

app      = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

byteBuffer       = np.zeros(2**15, dtype='uint8')
byteBufferLength = 0

MAX_RANGE  = 2.7   # metres
MAX_VEL    = 1.0   # m/s

# ─────────────────────────────────────────────────────────────────────────────
# HTML — Radar sweep topology map
# ─────────────────────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>IWR1443 Radar Map</title>
  <script src="https://cdn.socket.io/4.6.0/socket.io.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0a0a0a; display: flex; flex-direction: column;
           align-items: center; font-family: monospace; color: #00ff88; }
    h2   { margin: 14px 0 4px; font-size: 20px; color: #00e5ff;
           letter-spacing: 2px; text-transform: uppercase; }
    #stats { font-size: 13px; color: #aaa; margin-bottom: 10px; }
    canvas { border: 1px solid #1a3a1a; border-radius: 4px; }
    #legend { margin-top: 10px; font-size: 12px; color: #555; }
  </style>
</head>
<body>
  <h2>&#x25c9; IWR1443BOOST — Radar Map</h2>
  <div id="stats">Waiting for sensor...</div>
  <canvas id="radar" width="700" height="700"></canvas>
  <div id="legend">Sensor at bottom-center &nbsp;|&nbsp; Each ring = 0.5m &nbsp;|&nbsp; Colour = doppler velocity</div>

  <script>
    const canvas  = document.getElementById('radar');
    const ctx     = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const CX = W / 2;           // sensor X centre
    const CY = H - 60;          // sensor at bottom
    const MAX_R = %(MAX_RANGE)s;
    const SCALE = (H - 100) / MAX_R;   // pixels per metre

    // ── Draw static radar background ─────────────────────────────────────────
    function drawBackground() {
        ctx.clearRect(0, 0, W, H);

        // fill
        ctx.fillStyle = '#050f05';
        ctx.fillRect(0, 0, W, H);

        // range rings every 0.5m
        for (let r = 0.5; r <= MAX_R; r += 0.5) {
            let px = r * SCALE;
            ctx.beginPath();
            ctx.arc(CX, CY, px, Math.PI, 0);   // top semicircle only
            ctx.strokeStyle = r %% 1 === 0 ? '#1a4a1a' : '#0f2a0f';
            ctx.lineWidth   = r %% 1 === 0 ? 1.5 : 0.8;
            ctx.stroke();

            // range label on major rings
            if (r %% 1 === 0) {
                ctx.fillStyle = '#2a6a2a';
                ctx.font = '11px monospace';
                ctx.fillText(r.toFixed(1) + 'm', CX + px + 4, CY - 4);
            }
        }

        // azimuth lines -60 -30 0 +30 +60 degrees
        [-60, -30, 0, 30, 60].forEach(deg => {
            let rad = (deg - 90) * Math.PI / 180;
            let ex  = CX + MAX_R * SCALE * Math.cos(rad);
            let ey  = CY + MAX_R * SCALE * Math.sin(rad);
            ctx.beginPath();
            ctx.moveTo(CX, CY);
            ctx.lineTo(ex, ey);
            ctx.strokeStyle = deg === 0 ? '#1a5a1a' : '#0f2a0f';
            ctx.lineWidth   = deg === 0 ? 1.5 : 0.8;
            ctx.stroke();
            if (deg !== 0) {
                ctx.fillStyle = '#2a5a2a';
                ctx.font = '11px monospace';
                ctx.fillText(deg + '°', ex - 8, ey - 6);
            }
        });

        // sensor icon at origin
        ctx.beginPath();
        ctx.arc(CX, CY, 7, 0, 2*Math.PI);
        ctx.fillStyle = '#00e5ff';
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 10px monospace';
        ctx.fillText('SENSOR', CX - 24, CY + 20);
    }

    // ── Velocity → colour (blue=away, red=towards, green=still) ──────────────
    function velToColor(doppler) {
        if (Math.abs(doppler) < 0.05) return '#00ff88';          // stationary
        let v = Math.min(Math.abs(doppler) / %(MAX_VEL)s, 1.0);
        if (doppler > 0) {
            // moving away — red
            let g = Math.floor(255 * (1 - v));
            return `rgb(255, ${g}, 30)`;
        } else {
            // moving towards — cyan/blue
            let r = Math.floor(80 * (1 - v));
            return `rgb(${r}, 220, 255)`;
        }
    }

    // ── Draw a detected object ────────────────────────────────────────────────
    function drawObject(range, azimuth_rad, doppler) {
        let px = CX + range * SCALE * Math.sin(azimuth_rad);
        let py = CY - range * SCALE * Math.cos(azimuth_rad);

        let color = velToColor(doppler);

        // glow
        let grd = ctx.createRadialGradient(px, py, 0, px, py, 20);
        grd.addColorStop(0, color.replace(')', ', 0.4)').replace('rgb', 'rgba'));
        grd.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.beginPath();
        ctx.arc(px, py, 20, 0, 2*Math.PI);
        ctx.fillStyle = grd;
        ctx.fill();

        // dot
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, 2*Math.PI);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();

        // range label
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 11px monospace';
        ctx.fillText(range.toFixed(2) + 'm', px + 10, py - 8);
        ctx.fillStyle = '#aaa';
        ctx.font = '10px monospace';
        ctx.fillText((doppler >= 0 ? '+' : '') + doppler.toFixed(2) + 'm/s', px + 10, py + 6);
    }

    // ── Sweep animation ───────────────────────────────────────────────────────
    let sweepAngle = -Math.PI;
    function drawSweep() {
        sweepAngle += 0.04;
        if (sweepAngle > 0) sweepAngle = -Math.PI;
        let grd = ctx.createConicalGradient
            ? null
            : null;
        // simple line sweep
        let ex = CX + MAX_R * SCALE * Math.cos(sweepAngle + Math.PI/2);
        let ey = CY + MAX_R * SCALE * Math.sin(sweepAngle + Math.PI/2);
        ctx.beginPath();
        ctx.moveTo(CX, CY);
        ctx.lineTo(ex, ey);
        ctx.strokeStyle = 'rgba(0,255,136,0.25)';
        ctx.lineWidth = 2;
        ctx.stroke();
        requestAnimationFrame(drawSweep);
    }
    drawSweep();

    // ── Socket data ───────────────────────────────────────────────────────────
    var socket = io();
    socket.on('radar_data', function(d) {
        document.getElementById('stats').innerText =
            'Frame #' + d.frame + '  |  Objects: ' + d.numObj +
            '  |  MaxRange: %(MAX_RANGE)sm  |  MaxVel: %(MAX_VEL)sm/s';

        drawBackground();

        for (let i = 0; i < d.numObj; i++) {
            drawObject(d.range[i], d.azimuth[i], d.doppler[i]);
        }
    });
  </script>
</body>
</html>
""" % {'MAX_RANGE': MAX_RANGE, 'MAX_VEL': MAX_VEL}


@app.route('/')
def index():
    return render_template_string(HTML)


# ─────────────────────────────────────────────────────────────────────────────
# Serial config
# ─────────────────────────────────────────────────────────────────────────────
def read_response(port, timeout=2.0):
    buffer = ''
    start  = time.time()
    while time.time() - start < timeout:
        if port.in_waiting:
            buffer += port.read(port.in_waiting).decode(errors='ignore')
            if 'Done' in buffer or 'Error' in buffer or 'not recognized' in buffer:
                break
        time.sleep(0.05)
    return buffer.strip()


def serialConfig(configFileName):
    print(f"[*] Opening CLI  port : {CLI_PORT}")
    print(f"[*] Opening Data port : {DATA_PORT}\n")
    CLIport  = serial.Serial(CLI_PORT,  BAUD_CLI,  timeout=1)
    Dataport = serial.Serial(DATA_PORT, BAUD_DATA, timeout=1)
    time.sleep(3)
    CLIport.reset_input_buffer()
    Dataport.reset_input_buffer()
    config = [line.rstrip('\r\n') for line in open(configFileName)]
    for line in config:
        line = line.strip()
        if line == '' or line.startswith('%'):
            continue
        CLIport.write((line + '\r\n').encode())
        response = read_response(CLIport, timeout=2.0)
        print(f"  [{'✓' if 'Done' in response else '✗'}] {line}")
    print("\n[✓] Sensor RUNNING\n")
    return CLIport, Dataport


def parseConfigFile(configFileName):
    configParameters = {}
    numTxAnt = 3
    config = [line.rstrip('\r\n') for line in open(configFileName)]
    for line in config:
        words = line.split()
        if not words or words[0].startswith('%'):
            continue
        if words[0] == 'profileCfg':
            startFreq        = int(float(words[2]))
            idleTime         = int(words[3])
            rampEndTime      = float(words[5])
            freqSlopeConst   = float(words[8])
            numAdcSamples    = int(words[10])
            digOutSampleRate = int(words[11])
            numAdcSamplesRoundTo2 = 1
            while numAdcSamples > numAdcSamplesRoundTo2:
                numAdcSamplesRoundTo2 *= 2
        elif words[0] == 'frameCfg':
            chirpStartIdx = int(words[1])
            chirpEndIdx   = int(words[2])
            numLoops      = int(words[3])
            numChirpsPerFrame = (chirpEndIdx - chirpStartIdx + 1) * numLoops
            configParameters['numDopplerBins']       = numChirpsPerFrame / numTxAnt
            configParameters['numRangeBins']         = numAdcSamplesRoundTo2
            configParameters['rangeIdxToMeters']     = (3e8 * digOutSampleRate * 1e3) / (2 * freqSlopeConst * 1e12 * numAdcSamplesRoundTo2)
            configParameters['dopplerResolutionMps'] = 3e8 / (2 * startFreq * 1e9 * (idleTime + rampEndTime) * 1e-6 * configParameters['numDopplerBins'] * numTxAnt)
            configParameters['maxRange']             = (300 * 0.9 * digOutSampleRate) / (2 * freqSlopeConst * 1e3)
            configParameters['maxVelocity']          = 3e8 / (4 * startFreq * 1e9 * (idleTime + rampEndTime) * 1e-6 * numTxAnt)
    print("[*] Radar Parameters:")
    for k, v in configParameters.items():
        print(f"    {k:<28} = {v:.4f}" if isinstance(v, float) else f"    {k:<28} = {v}")
    print()
    return configParameters


# ─────────────────────────────────────────────────────────────────────────────
# TLV parser — SDK 1.x
# ─────────────────────────────────────────────────────────────────────────────
def readAndParseData14xx(Dataport, configParameters):
    global byteBuffer, byteBufferLength

    MMWDEMO_UART_MSG_DETECTED_POINTS = 1
    maxBufferSize = 2**15
    magicWord     = [2, 1, 4, 3, 6, 5, 8, 7]

    magicOK = dataOK = 0
    frameNumber = 0
    detObj = {}

    readBuffer = Dataport.read(Dataport.in_waiting or 1)
    byteVec    = np.frombuffer(readBuffer, dtype='uint8')
    byteCount  = len(byteVec)

    if (byteBufferLength + byteCount) < maxBufferSize:
        byteBuffer[byteBufferLength:byteBufferLength + byteCount] = byteVec
        byteBufferLength += byteCount

    if byteBufferLength > 16:
        possibleLocs = np.where(byteBuffer == magicWord[0])[0]
        startIdx = [loc for loc in possibleLocs
                    if np.all(byteBuffer[loc:loc+8] == magicWord)]
        if startIdx:
            if startIdx[0] > 0:
                byteBuffer[:byteBufferLength-startIdx[0]] = byteBuffer[startIdx[0]:byteBufferLength]
                byteBuffer[byteBufferLength-startIdx[0]:] = 0
                byteBufferLength -= startIdx[0]
            if byteBufferLength < 0:
                byteBufferLength = 0
            word = [1, 2**8, 2**16, 2**24]
            totalPacketLen = np.matmul(byteBuffer[12:16], word)
            if byteBufferLength >= totalPacketLen > 0:
                magicOK = 1

    if magicOK:
        word = [1, 2**8, 2**16, 2**24]
        idX  = 0
        idX += 8; idX += 4
        totalPacketLen = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        idX += 4
        frameNumber    = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        idX += 4
        numDetectedObj = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        numTLVs        = np.matmul(byteBuffer[idX:idX+4], word); idX += 4

        dataOK = 1

        for _ in range(numTLVs):
            word       = [1, 2**8, 2**16, 2**24]
            tlv_type   = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
            tlv_length = np.matmul(byteBuffer[idX:idX+4], word); idX += 4

            if tlv_type == MMWDEMO_UART_MSG_DETECTED_POINTS:
                word2          = [1, 2**8]
                tlv_numObj     = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                tlv_xyzQFormat = 2 ** np.matmul(byteBuffer[idX:idX+2], word2); idX += 2

                rangeIdx   = np.zeros(tlv_numObj, dtype='uint16')
                dopplerIdx = np.zeros(tlv_numObj, dtype='uint16')
                peakVal    = np.zeros(tlv_numObj, dtype='uint16')
                x = np.zeros(tlv_numObj, dtype='uint16')
                y = np.zeros(tlv_numObj, dtype='uint16')
                z = np.zeros(tlv_numObj, dtype='uint16')

                for n in range(tlv_numObj):
                    rangeIdx[n]   = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    dopplerIdx[n] = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    peakVal[n]    = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    x[n]          = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    y[n]          = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    z[n]          = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2

                rangeIdx   = rangeIdx.astype('int16')
                dopplerIdx = dopplerIdx.astype('int32')
                x_m = x.astype('int16') / tlv_xyzQFormat
                y_m = y.astype('int16') / tlv_xyzQFormat

                rangeVal = rangeIdx * configParameters['rangeIdxToMeters']
                dopplerIdx[dopplerIdx > (configParameters['numDopplerBins'] / 2 - 1)] -= 65536
                dopplerVal = dopplerIdx * configParameters['dopplerResolutionMps']

                # Azimuth angle from x and range: atan2(x, range)
                azimuth = np.arctan2(x_m, rangeVal)

                detObj = {
                    'numObj' : tlv_numObj,
                    'range'  : rangeVal,
                    'doppler': dopplerVal,
                    'x'      : x_m,
                    'y'      : y_m,
                    'azimuth': azimuth
                }

        if idX > 0 and byteBufferLength > idX:
            shiftSize = totalPacketLen
            byteBuffer[:byteBufferLength-shiftSize] = byteBuffer[shiftSize:byteBufferLength]
            byteBuffer[byteBufferLength-shiftSize:] = 0
            byteBufferLength -= shiftSize
            if byteBufferLength < 0:
                byteBufferLength = 0

    return dataOK, frameNumber, detObj


# ─────────────────────────────────────────────────────────────────────────────
# Radar thread
# ─────────────────────────────────────────────────────────────────────────────
def radar_thread(Dataport, configParameters):
    print("[*] Radar thread started")
    while True:
        try:
            dataOK, frameNumber, detObj = readAndParseData14xx(Dataport, configParameters)
            if dataOK:
                if detObj and detObj['numObj'] > 0:
                    n = int(detObj['numObj'])
                    payload = {
                        'frame'  : int(frameNumber),
                        'numObj' : n,
                        'range'  : [round(float(v), 3) for v in detObj['range']],
                        'doppler': [round(float(v), 3) for v in detObj['doppler']],
                        'azimuth': [round(float(v), 4) for v in detObj['azimuth']],
                        'x'      : [round(float(v), 3) for v in detObj['x']],
                    }
                    print(f"Frame #{frameNumber:05d} | Objects: {n}")
                    for i in range(n):
                        print(f"  [{i}] range={detObj['range'][i]:.2f}m  "
                              f"az={np.degrees(detObj['azimuth'][i]):.1f}°  "
                              f"doppler={detObj['doppler'][i]:.2f}m/s")
                else:
                    payload = {'frame': int(frameNumber), 'numObj': 0,
                               'range': [], 'doppler': [], 'azimuth': [], 'x': []}
                    print(f"Frame #{frameNumber:05d} | Objects: 0", end='\r')

                socketio.emit('radar_data', payload)
            time.sleep(0.033)
        except Exception as e:
            print(f"\n[!] Error: {e}")
            time.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    CLIport, Dataport = serialConfig(CONFIG_FILE)
    configParameters  = parseConfigFile(CONFIG_FILE)

    t = threading.Thread(target=radar_thread, args=(Dataport, configParameters), daemon=True)
    t.start()

    print("[*] Open http://<raspberry-pi-ip>:5000 in your browser\n")
    try:
        socketio.run(app, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n[*] Stopping...")
        CLIport.write(b'sensorStop\r\n')
        CLIport.close()
        Dataport.close()
