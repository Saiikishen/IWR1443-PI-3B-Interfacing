# IWR1443 Vital Signs Web Dashboard

A browser-based vital signs monitor for **TI IWR1443BOOST** that reads mmWave UART data, decodes the TI vital-signs output packet, and renders breathing rate, heart rate, waveforms, confidence metrics, range-bin information, and motion status in real time.

This README is written for the Python application `vital_signs-3.py`, which is the main runtime script in this project.

---

## Highlights

- Web dashboard using Flask + Socket.IO
- Linux serial device support via `/dev/iwr_cli` and `/dev/iwr_data`
- Dynamic packet-length calculation from the radar `.cfg` file
- MATLAB-compatible packet parsing for `VitalSignsDemo_OutputStats`
- Real-time breathing and heartbeat waveform rendering
- Motion flag display for large body movement detection

---

## Files

```text
.
├── vital_signs-3.py                   # Main Python web dashboard
├── profile_2d_VitalSigns_20fps.cfg    # Active radar configuration
├── vitalSigns_demo_gui.m              # TI MATLAB reference implementation
├── readData_IWR1443.py                # Older Python serial parsing example
└── readme.txt                         # MATLAB Runtime deployment note
```

---

## System overview

The application configures the radar through a CLI UART at 115200 baud and reads binary monitoring packets from a data UART at 921600 baud. It serves an HTML dashboard over Flask and pushes live sensor updates to the browser using Socket.IO.

The Python parser intentionally mirrors the TI MATLAB implementation so the packet layout, offsets, and field meanings stay aligned with the reference vital-signs demo.

---

## Hardware requirements

- TI IWR1443BOOST radar board
- Linux host or Raspberry Pi
- USB connection exposing two serial interfaces
- Stationary test subject positioned in front of the radar

---

## Software requirements

- Python 3.x
- `pyserial`
- `numpy`
- `flask`
- `flask-socketio`

Install dependencies with:

```bash
pip install pyserial numpy flask flask-socketio
```

---

## Serial configuration

The script uses these defaults:

```python
CONFIG_FILE = '/home/iiot1/Radar/profile_2d_VitalSigns_20fps.cfg'
CLI_PORT    = '/dev/iwr_cli'
DATA_PORT   = '/dev/iwr_data'
BAUD_CLI    = 115200
BAUD_DATA   = 921600
```

Update these values in `vital_signs-3.py` if your serial device names or file paths are different.

---

## Radar configuration

The supplied configuration file is `profile_2d_VitalSigns_20fps.cfg`.

### Important commands

- `frameCfg 0 0 2 0 50 1 0` → 50 ms frame periodicity, or 20 frames per second
- `vitalSignsCfg 0.3 1.0 256 512 4 0.1 0.8 2000 5000` → vital-sign processing with a target range window from 0.3 m to 1.0 m
- `motionDetection 0 20 3.0` → enables large-motion detection
- `guiMonitor 1 0 0 1` → enables the monitored UART output required by the demo

The Python script parses this config file at startup and computes the exact UART packet length using the same formulas used in the MATLAB `parseCfg()` flow.

---

## Why this script matters

Earlier versions of the parser assumed a hardcoded packet length of 288 bytes. That only works when the number of processed range bins happens to match that specific configuration.

`vital_signs-3.py` fixes this by calculating `PKTLEN` from the active radar config, so the receive buffer stays aligned even when the range window changes. This is especially important for fields like `motionFlag`, because a wrong packet length causes fixed byte offsets to point into the wrong part of the packet.

---

## Application flow

1. Read the radar config file.
2. Compute the correct packet length from profile and vital-sign range settings.
3. Open the CLI and data serial ports.
4. Send the config file line by line to the radar.
5. Start a background thread that reads the data UART.
6. Search for the TI magic word in the byte stream.
7. Decode the frame fields from fixed MATLAB-compatible offsets.
8. Emit parsed values to the browser over Socket.IO.
9. Render live cards and waveforms in the dashboard.

---

## Packet format

The parser follows the MATLAB demo constants:

- Header length: 40 bytes
- TLV header length: 8 bytes
- Vital-sign stats block: 128 bytes
- Packet padding: multiple of 32 bytes

The packet layout used by `vital_signs-3.py` is:

```text
Bytes   0-39   Frame header
Bytes  40-47   TLV-1 header
Bytes  48-175  VitalSignsDemo_OutputStats (128 bytes)
Bytes 176-183  TLV-2 header
Bytes 184+     Range profile payload
Tail           Padding to 32-byte alignment
```

The script documents that the vital-sign stats block always begins at byte offset 48, while the total packet length changes with the configured number of processed range bins.

---

## Decoded fields

The Python script maps the following fields from `VitalSignsDemo_OutputStats`:

| Field | Byte offset | Description |
|---|---:|---|
| `B_RANGE_BIN_VALUE` | 52 | Max range-bin value |
| `B_PHASE_UNWRAP` | 64 | Unwrapped phase |
| `B_BREATH_WFM` | 68 | Breathing waveform filter output |
| `B_HEART_WFM` | 72 | Heart waveform filter output |
| `B_HEART_FFT` | 76 | Heart-rate FFT estimate |
| `B_HEART_FFT_4HZ` | 80 | Heart-rate FFT 4 Hz estimate |
| `B_HEART_XCORR` | 84 | Heart-rate xCorr estimate |
| `B_HEART_PEAK` | 88 | Heart-rate peak estimate |
| `B_BREATH_FFT` | 92 | Breathing-rate FFT estimate |
| `B_BREATH_XCORR` | 96 | Breathing-rate xCorr estimate |
| `B_BREATH_PEAK` | 100 | Breathing-rate peak estimate |
| `B_CM_BREATH` | 104 | Breathing confidence metric |
| `B_CM_HEART` | 112 | Heart confidence metric |
| `B_ENERGY_BREATH` | 124 | Breathing waveform energy |
| `B_ENERGY_HEART` | 128 | Heart waveform energy |
| `B_MOTION_FLAG` | 132 | Motion-detected flag |

The script also reads the range-bin index from byte offset 50 and the frame number from byte offset 20.

---

## MATLAB alignment

The field definitions in `vital_signs-3.py` are derived from the same index mapping used by `vitalSigns_demo_gui.m`, including `INDEX_BREATHING_WAVEFORM = 6`, `INDEX_HEART_WAVEFORM = 7`, `INDEX_HEART_RATE_EST_FFT = 8`, `INDEX_BREATHING_RATE_FFT = 12`, `INDEX_CONFIDENCE_METRIC_BREATH = 15`, `INDEX_CONFIDENCE_METRIC_HEART = 17`, and `INDEX_MOTION_DETECTION = 22`.

The MATLAB GUI also confirms that the breathing and heart-rate values are displayed directly from the parsed fields, and that motion is flagged separately from the normal micro-motion caused by breathing and heartbeat.

---

## Web dashboard

The browser UI shows:

- Breathing rate
- Heart rate
- Breathing confidence metric
- Heart confidence metric
- Range bin
- Breathing and heart waveform energy
- Motion flag
- Breathing waveform plot
- Heartbeat waveform plot

The dashboard is served on port `5000` and updated continuously through Socket.IO events named `vital_signs`.

---

## Running the application

Start the script with:

```bash
python3 vital_signs-3.py
```

Then open the dashboard in a browser:

```text
http://<your-device-ip>:5000
```

If the script is being run locally on the same machine, you can also use:

```text
http://localhost:5000
```

---

## Expected console output

At startup, the script prints the computed packet information from the config file, including range-bin size, number of processed bins, and packet length.

During runtime, it prints frame-by-frame decoded values similar to:

```text
Frame #00577 | Breath: 15.2 bpm Heart: 68.0 bpm CM_b=0.82 CM_h=0.35 motion=0.0 (still)
```

---

## Motion flag behavior

The motion flag is for **large body motion**, not chest micro-motion. A value near `0` means the subject is still enough for reliable vital-sign extraction, and a value near `1` means significant movement was detected.

So a motion flag of `0` while breathing and heart waveforms are visible is normal and desirable.

---

## Measurement guidelines

- Place the subject within 0.3 m to 1.0 m of the radar.
- Keep the chest facing the sensor.
- Sit as still as possible during measurement.
- Allow several seconds for the rates to stabilize.
- Avoid strong moving objects in the field of view.

---

## Troubleshooting

### No values on the dashboard

- Check that `/dev/iwr_cli` and `/dev/iwr_data` are correct.
- Make sure the config file path is valid.
- Confirm the radar accepted all CLI commands.
- Verify the browser can reach port 5000.

### Wrong heart or breathing values

- Verify the parser is using the dynamic packet length from the config file.
- Confirm the field offsets match the MATLAB reference mapping.
- Check that the subject is inside the configured range window.

### Motion flag stuck at zero

- Small chest motion will not trigger it.
- Try a larger arm or torso movement.
- Confirm packet-length calculation is correct, since buffer desync can corrupt fixed-offset fields.

### Flat waveforms

- Confirm the subject is in front of the sensor.
- Check serial data flow and packet synchronization.
- Verify the correct radar config was loaded before streaming.

---

## Reference files

- `vital_signs-3.py` — main Python application
- `profile_2d_VitalSigns_20fps.cfg` — active radar configuration
- `vitalSigns_demo_gui.m` — TI MATLAB reference parser and GUI
- `readData_IWR1443.py` — older Python parser example

---

## Notes

This README is tailored to the current Python web-dashboard implementation rather than the older PyQtGraph example. If you later add screenshots, systemd service files, udev rules, or a wiring diagram, this document can be extended into a full GitHub project README.
