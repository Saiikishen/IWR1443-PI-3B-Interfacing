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

# ─────────────────────────────────────────────────────────────────────────────
# HTML — Live Plotly scatter
# ─────────────────────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>IWR1443 Live Radar</title>
    <script src="https://cdn.socket.io/4.6.0/socket.io.min.js"></script>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { background:#111; color:#eee; font-family:monospace; text-align:center; }
        h2   { color:#00e5ff; margin:10px; }
        #stats { font-size:14px; color:#aaa; margin-bottom:5px; }
    </style>
</head>
<body>
    <h2>IWR1443BOOST — Live Point Cloud</h2>
    <div id="stats">Waiting for frames...</div>
    <div id="plot" style="width:700px;height:600px;margin:auto;"></div>
    <script>
        var layout = {
            paper_bgcolor:'#111', plot_bgcolor:'#1a1a2e',
            xaxis:{ title:'X (m)', range:[-3,3], autorange:false,
                    color:'#eee', gridcolor:'#333', zeroline:true, zerolinecolor:'#555' },
            yaxis:{ title:'Range (m)', range:[0,3], autorange:false,
                    color:'#eee', gridcolor:'#333' },
            margin:{ t:20, b:60, l:60, r:20 },
            font:{ color:'#eee' }
        };
        var trace = [{
            x:[], y:[], mode:'markers',
            marker:{ color:'#00e5ff', size:12, opacity:0.9,
                     line:{ color:'#fff', width:1 } }
        }];
        Plotly.newPlot('plot', trace, layout);

        var socket = io();
        socket.on('radar_data', function(d) {
            document.getElementById('stats').innerText =
                'Frame #' + d.frame + ' | Objects: ' + d.numObj +
                ' | MaxRange: 2.7m | MaxVel: 1.0 m/s';
            Plotly.restyle('plot', { x:[d.x], y:[d.y] }, [0]);
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)


# ─────────────────────────────────────────────────────────────────────────────
# Serial config  (unchanged from working radar_read.py)
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
        if 'Done' in response:
            print(f"  [✓] {line}")
        else:
            print(f"  [✗] {line} → {repr(response)}")
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
# TLV parser — EXACTLY as in working radar_read.py
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
        idX += 8
        idX += 4
        totalPacketLen = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        idX += 4
        frameNumber    = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        idX += 4
        numDetectedObj = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        numTLVs        = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        # SDK 1.x — no subFrameNumber

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

                # Signed cast BEFORE any arithmetic
                rangeIdx   = rangeIdx.astype('int16')
                dopplerIdx = dopplerIdx.astype('int32')
                x = x.astype('int16') / tlv_xyzQFormat
                y = y.astype('int16') / tlv_xyzQFormat
                z = z.astype('int16') / tlv_xyzQFormat

                rangeVal = rangeIdx * configParameters['rangeIdxToMeters']
                dopplerIdx[dopplerIdx > (configParameters['numDopplerBins'] / 2 - 1)] -= 65536
                dopplerVal = dopplerIdx * configParameters['dopplerResolutionMps']

                detObj = {
                    'numObj'  : tlv_numObj,
                    'range'   : rangeVal,
                    'doppler' : dopplerVal,
                    'peakVal' : peakVal,
                    'x'       : x,
                    'y'       : y,
                    'z'       : z
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
# Radar thread — parse + emit to browser
# ─────────────────────────────────────────────────────────────────────────────
def radar_thread(Dataport, configParameters):
    print("[*] Radar thread started")
    while True:
        try:
            dataOK, frameNumber, detObj = readAndParseData14xx(Dataport, configParameters)
            if dataOK:
                if detObj and detObj['numObj'] > 0:
                    # Use x (lateral) vs range (depth) for top-down view
                    plot_x = detObj['x'].tolist()
                    plot_y = detObj['range'].tolist()
                    n = detObj['numObj']
                    print(f"Frame #{frameNumber:05d} | Objects: {n}")
                    for i in range(n):
                        print(f"  obj[{i}]  range={detObj['range'][i]:.2f}m  "
                              f"doppler={detObj['doppler'][i]:.2f}m/s  "
                              f"x={detObj['x'][i]:.2f}  y={detObj['y'][i]:.2f}")
                else:
                    plot_x, plot_y, n = [], [], 0
                    print(f"Frame #{frameNumber:05d} | Objects: 0", end='\r')

                socketio.emit('radar_data', {
                    'frame' : int(frameNumber),
                    'numObj': int(n),
                    'x'     : plot_x,
                    'y'     : plot_y
                })
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
