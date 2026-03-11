import serial
import time
import numpy as np

# ─── Configuration ────────────────────────────────────────────────────────────
CONFIG_FILE = '/home/iiot1/Radar/iwr1443_config.cfg'
CLI_PORT    = '/dev/iwr_cli'
DATA_PORT   = '/dev/iwr_data'
BAUD_CLI    = 115200
BAUD_DATA   = 921600

# ─── Global byte buffer ───────────────────────────────────────────────────────
byteBuffer       = np.zeros(2**15, dtype='uint8')
byteBufferLength = 0


# ─────────────────────────────────────────────────────────────────────────────
# 1. serialConfig
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

    time.sleep(3)                    # wait for XDS110 bootloader to finish
    CLIport.reset_input_buffer()
    Dataport.reset_input_buffer()

    config = [line.rstrip('\r\n') for line in open(configFileName)]
    all_ok = True

    for line in config:
        line = line.strip()
        if line == '' or line.startswith('%'):
            continue

        CLIport.write((line + '\r\n').encode())
        response = read_response(CLIport, timeout=2.0)

        if 'Done' in response:
            print(f"  [✓] {line}")
        elif 'Error' in response or 'not recognized' in response:
            print(f"  [✗] {line}")
            print(f"       Response: {repr(response)}")
            all_ok = False
        else:
            print(f"  [?] {line}  →  {repr(response)}")
            all_ok = False

    if all_ok:
        print("\n[✓] Sensor configured and RUNNING.\n")
    else:
        print("\n[!] Some commands failed — check config or firmware version.\n")

    return CLIport, Dataport


# ─────────────────────────────────────────────────────────────────────────────
# 2. parseConfigFile — SDK 1.x
# ─────────────────────────────────────────────────────────────────────────────
def parseConfigFile(configFileName):
    configParameters = {}
    numRxAnt = 4
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
            configParameters['numDopplerBins']        = numChirpsPerFrame / numTxAnt
            configParameters['numRangeBins']          = numAdcSamplesRoundTo2
            configParameters['rangeResolutionMeters'] = (3e8 * digOutSampleRate * 1e3) / (2 * freqSlopeConst * 1e12 * numAdcSamples)
            configParameters['rangeIdxToMeters']      = (3e8 * digOutSampleRate * 1e3) / (2 * freqSlopeConst * 1e12 * numAdcSamplesRoundTo2)
            configParameters['dopplerResolutionMps']  = 3e8 / (2 * startFreq * 1e9 * (idleTime + rampEndTime) * 1e-6 * configParameters['numDopplerBins'] * numTxAnt)
            configParameters['maxRange']              = (300 * 0.9 * digOutSampleRate) / (2 * freqSlopeConst * 1e3)
            configParameters['maxVelocity']           = 3e8 / (4 * startFreq * 1e9 * (idleTime + rampEndTime) * 1e-6 * numTxAnt)

    print("[*] Radar Parameters:")
    for k, v in configParameters.items():
        print(f"    {k:<30} = {v:.4f}" if isinstance(v, float) else f"    {k:<30} = {v}")
    print()
    return configParameters


# ─────────────────────────────────────────────────────────────────────────────
# 3. readAndParseData14xx — SDK 1.x (no subFrameNumber in header)
# ─────────────────────────────────────────────────────────────────────────────
def readAndParseData14xx(Dataport, configParameters):
    global byteBuffer, byteBufferLength

    MMWDEMO_UART_MSG_DETECTED_POINTS = 1
    maxBufferSize = 2**15
    magicWord     = [2, 1, 4, 3, 6, 5, 8, 7]

    magicOK     = 0
    dataOK      = 0
    frameNumber = 0
    detObj      = {}

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
                byteBuffer[:byteBufferLength - startIdx[0]] = byteBuffer[startIdx[0]:byteBufferLength]
                byteBuffer[byteBufferLength - startIdx[0]:] = 0
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

        idX += 8                                                              # magic number
        idX += 4                                                              # version
        totalPacketLen = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        idX += 4                                                              # platform
        frameNumber    = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        idX += 4                                                              # timeCpuCycles
        numDetectedObj = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        numTLVs        = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
        # SDK 1.x: NO subFrameNumber field here
        # SDK 2.x: uncomment below
        # subFrameNumber = np.matmul(byteBuffer[idX:idX+4], word); idX += 4

        dataOK = 1   # valid frame header received

        for _ in range(numTLVs):
            word       = [1, 2**8, 2**16, 2**24]
            tlv_type   = np.matmul(byteBuffer[idX:idX+4], word); idX += 4
            tlv_length = np.matmul(byteBuffer[idX:idX+4], word); idX += 4

            if tlv_type == MMWDEMO_UART_MSG_DETECTED_POINTS:
                word2          = [1, 2**8]
                tlv_numObj     = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                tlv_xyzQFormat = 2 ** np.matmul(byteBuffer[idX:idX+2], word2); idX += 2

                rangeIdx   = np.zeros(tlv_numObj, dtype='int16')
                dopplerIdx = np.zeros(tlv_numObj, dtype='int16')
                peakVal    = np.zeros(tlv_numObj, dtype='int16')
                x = np.zeros(tlv_numObj, dtype='int16')
                y = np.zeros(tlv_numObj, dtype='int16')
                z = np.zeros(tlv_numObj, dtype='int16')

                for n in range(tlv_numObj):
                    rangeIdx[n]   = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    dopplerIdx[n] = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    peakVal[n]    = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    x[n]          = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    y[n]          = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2
                    z[n]          = np.matmul(byteBuffer[idX:idX+2], word2); idX += 2

                rangeVal = rangeIdx * configParameters['rangeIdxToMeters']
                dopplerIdx[dopplerIdx > (configParameters['numDopplerBins'] / 2 - 1)] -= 65535
                dopplerVal = dopplerIdx * configParameters['dopplerResolutionMps']
                x = x / tlv_xyzQFormat
                y = y / tlv_xyzQFormat
                z = z / tlv_xyzQFormat

                detObj = {
                    'numObj'    : tlv_numObj,
                    'rangeIdx'  : rangeIdx,
                    'range'     : rangeVal,
                    'dopplerIdx': dopplerIdx,
                    'doppler'   : dopplerVal,
                    'peakVal'   : peakVal,
                    'x'         : x,
                    'y'         : y,
                    'z'         : z
                }

        # Flush processed packet from buffer
        if idX > 0 and byteBufferLength > idX:
            shiftSize = totalPacketLen
            byteBuffer[:byteBufferLength - shiftSize] = byteBuffer[shiftSize:byteBufferLength]
            byteBuffer[byteBufferLength - shiftSize:] = 0
            byteBufferLength -= shiftSize
            if byteBufferLength < 0:
                byteBufferLength = 0

    return dataOK, frameNumber, detObj


# ─────────────────────────────────────────────────────────────────────────────
# 4. Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    CLIport, Dataport = serialConfig(CONFIG_FILE)
    configParameters  = parseConfigFile(CONFIG_FILE)

    print("[*] Listening for radar frames  (Ctrl+C to stop)...\n")
    frameData    = {}
    currentIndex = 0

    try:
        while True:
            dataOK, frameNumber, detObj = readAndParseData14xx(Dataport, configParameters)

            if dataOK:
                currentIndex += 1
                if detObj and detObj['numObj'] > 0:
                    print(f"Frame #{frameNumber:05d} | Objects: {detObj['numObj']}")
                    for i in range(detObj['numObj']):
                        print(f"  obj[{i}]  range={detObj['range'][i]:.2f}m  "
                              f"doppler={detObj['doppler'][i]:.2f}m/s  "
                              f"x={detObj['x'][i]:.2f}  y={detObj['y'][i]:.2f}  z={detObj['z'][i]:.2f}")
                else:
                    print(f"Frame #{frameNumber:05d} | Objects: 0", end='\r')

            time.sleep(0.033)   # ~30 Hz

    except KeyboardInterrupt:
        print("\n[*] Stopping sensor...")
        CLIport.write(b'sensorStop\r\n')
        CLIport.close()
        Dataport.close()
        print("[✓] Done.")
