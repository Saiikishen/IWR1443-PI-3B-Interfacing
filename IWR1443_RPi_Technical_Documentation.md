# IWR1443BOOST + Raspberry Pi 3B+ Integration and Visualization

## 1. Objective

This document describes the complete technical process for interfacing a **TI IWR1443BOOST** mmWave radar with a **Raspberry Pi 3 Model B+**, configuring the radar over the CLI UART, reading radar TLV data frames over the high-speed data UART, parsing detected object information, and visualizing detections in real time through a browser-based radar map.

The workflow documented here reflects a practical integration session and includes the debugging steps required to make the setup reliable on a Raspberry Pi accessed over SSH.

---

## 2. System Architecture

```text
+------------------+        USB (XDS110 composite device)        +-----------------------+
|  IWR1443BOOST    |  <--------------------------------------->  | Raspberry Pi 3B+      |
|                  |                                             |                       |
| CLI Port         |  /dev/ttyACM0  -> /dev/iwr_cli             | Python serial config  |
| Data Port        |  /dev/ttyACM1  -> /dev/iwr_data            | TLV parser            |
| mmWave Demo FW   |                                             | Flask/SocketIO server |
+------------------+                                             +-----------+-----------+
                                                                            |
                                                                            |
                                                                            v
                                                                +-----------------------+
                                                                | Laptop Browser        |
                                                                | Radar map / point map |
                                                                +-----------------------+
```

---

## 3. Hardware Used

- **Radar board:** TI IWR1443BOOST
- **Host SBC:** Raspberry Pi 3 Model B+
- **Access method:** SSH from laptop to Raspberry Pi
- **USB interface:** Onboard XDS110 debugger / USB bridge on IWR1443BOOST
- **Operating mode:** Headless Raspberry Pi, browser-based visualization from laptop

---

## 4. Important Interface Facts

### 4.1 The IWR1443BOOST USB interface

The IWR1443BOOST does **not** appear as a generic `cp210x` USB-UART device in this setup. Instead, the onboard XDS110 exposes two **CDC-ACM** serial interfaces on Linux:

- `/dev/ttyACM0` → CLI/configuration port
- `/dev/ttyACM1` → binary data port

During setup, persistent symbolic links were created:

- `/dev/iwr_cli` → CLI port
- `/dev/iwr_data` → Data port

### 4.2 UART roles

| Port | Purpose | Baud Rate |
|---|---|---:|
| CLI | Send config commands to radar firmware | 115200 |
| Data | Receive binary TLV frames | 921600 |

### 4.3 CLI line endings

The radar CLI must be sent with **CRLF** line endings:

```python
port.write((command + '\r\n').encode())
```

Using only `\n` can lead to echo-only behavior without proper command execution.

---

## 5. Firmware and SDK Compatibility

### 5.1 Key lesson from bring-up

A major part of the debugging process was caused by **firmware/config mismatches**.

Observed behavior showed that the board accepted **SDK 1.x style CLI syntax**, not the newer SDK 2.x/3.x syntax used in some later examples.

### 5.2 How the mismatch was identified

The following symptoms were observed when using newer config syntax:

- Commands with subframe index `-1` such as:
  - `adcbufCfg -1 ...`
  - `guiMonitor -1 ...`
  - `cfarCfg -1 ...`
  - `clutterRemoval -1 ...`
  returned **"Invalid usage of the CLI command"**
- Commands such as:
  - `extendedMaxVelocity`
  - `lvdsStreamCfg`
  - `aoaFovCfg`
  - `cfarFovCfg`
  returned **"not recognized as a CLI command"**

This confirmed that the running demo firmware expected the **older CLI command set**.

### 5.3 Working conclusion

The stable working configuration for this integration used the older xWR14xx mmWave demo configuration format, and the parser was adjusted accordingly.

---

## 6. Raspberry Pi Setup

### 6.1 Update system and install packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv git -y
```

### 6.2 Create Python virtual environment

```bash
python3 -m venv ~/radar_env
source ~/radar_env/bin/activate
pip install pyserial numpy flask flask-socketio
```

### 6.3 Add serial permissions

```bash
sudo usermod -aG dialout $USER
```

After this, log out and reconnect over SSH.

---

## 7. Verifying USB Enumeration

After connecting the IWR1443BOOST via USB, the Pi should show two ACM devices:

```bash
ls /dev/ttyACM*
```

Expected:

```bash
/dev/ttyACM0  /dev/ttyACM1
```

The kernel log should show XDS110 and `cdc_acm` attachment events:

```bash
dmesg | grep -i "acm\|xds\|0451"
```

---

## 8. Persistent Device Naming with udev

To avoid device-name swaps after reboot, udev rules were created using environment variables derived from the USB device.

### 8.1 Rule file

`/etc/udev/rules.d/99-iwr1443.rules`

```udev
# IWR1443 CLI Port
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="0451", ENV{ID_MODEL_ID}=="bef3", ENV{ID_USB_INTERFACE_NUM}=="00", SYMLINK+="iwr_cli", GROUP="dialout", MODE="0666"

# IWR1443 Data Port
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="0451", ENV{ID_MODEL_ID}=="bef3", ENV{ID_USB_INTERFACE_NUM}=="03", SYMLINK+="iwr_data", GROUP="dialout", MODE="0666"
```

### 8.2 Reload rules

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
```

If the symlinks do not appear immediately, unplug and replug the board.

### 8.3 Verify

```bash
ls -l /dev/iwr*
```

Expected:

```bash
/dev/iwr_cli  -> ttyACM0
/dev/iwr_data -> ttyACM1
```

---

## 9. Working Radar Configuration File

The working configuration used the older mmWave demo syntax and successfully started the radar.

### 9.1 Working config example

```cfg
sensorStop
flushCfg
dfeDataOutputMode 1
channelCfg 15 7 0
adcCfg 2 1
adcbufCfg 0 1 0 1
profileCfg 0 77 284 7 40 0 0 100 1 64 2000 0 0 30
chirpCfg 0 0 0 0 0 0 0 1
chirpCfg 1 1 0 0 0 0 0 4
chirpCfg 2 2 0 0 0 0 0 2
frameCfg 0 2 16 0 50 1 0
lowPower 0 0
guiMonitor 1 0 0 0 0 0
cfarCfg 0 2 8 4 3 0 1195
peakGrouping 1 0 0 1 56
multiObjBeamForming 1 0.5
clutterRemoval 1
calibDcRangeSig 0 -5 8 256
compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0
measureRangeBiasAndRxChanPhase 0 1.5 0.2
CQRxSatMonitor 0 3 4 99 0
CQSigImgMonitor 0 31 4
analogMonitor 1 1
sensorStart
```

### 9.2 Config-derived radar parameters

From this config, the parser calculated:

- Number of Doppler bins: 16
- Number of range bins: 64
- Range resolution: about 0.0469 m
- Maximum range: about 2.7 m
- Maximum radial velocity: about 1.0 m/s

---

## 10. Sending the Configuration from Python

### 10.1 Requirements

The Python sender must:

- open both serial ports
- wait for the XDS110 and radar firmware to settle
- flush stale bytes from the serial buffers
- send each config line with `\r\n`
- read the CLI response and confirm `Done`

### 10.2 Important startup detail

A startup delay of around **3 seconds** before sending commands was required for stable behavior.

### 10.3 Command-response interpretation

Healthy responses look like:

```text
[✓] frameCfg 0 2 16 0 50 1 0
[✓] sensorStart
```

If a command returns:

- **Invalid usage** → wrong syntax for the running demo firmware
- **not recognized** → command unsupported by running firmware
- empty string or echo only → board not ready, line ending wrong, or firmware crash occurred previously

---

## 11. Reading Data Frames

### 11.1 Data protocol

The high-speed data port transmits **binary TLV packets** beginning with a magic word:

```text
02 01 04 03 06 05 08 07
```

Because serial reads are chunked, this magic word may appear split across adjacent reads. A rolling byte buffer is therefore required.

### 11.2 Parser structure

The parser performs these steps:

1. Read all available bytes from the data port
2. Append them to a persistent byte buffer
3. Search for the magic word
4. Align the buffer to the packet start
5. Read the packet header
6. Iterate through TLVs
7. Extract detected object fields
8. Remove the processed frame from the buffer

### 11.3 Important header difference

For the working setup, the header was treated as **SDK 1.x style**, meaning **no `subFrameNumber` field** was parsed in the frame header.

Trying to parse a `subFrameNumber` in this firmware misaligned all following TLV offsets.

---

## 12. Detected Object Fields

For each detected object, the parser extracts:

- `rangeIdx`
- `dopplerIdx`
- `peakVal`
- `x`
- `y`
- `z`

These are then converted into engineering values.

### 12.1 Range conversion

```python
rangeVal = rangeIdx * configParameters['rangeIdxToMeters']
```

### 12.2 Doppler conversion

Unsigned Doppler bins must be corrected into signed values before scaling:

```python
dopplerIdx = dopplerIdx.astype('int32')
dopplerIdx[dopplerIdx > (numDopplerBins / 2 - 1)] -= 65536
dopplerVal = dopplerIdx * dopplerResolutionMps
```

### 12.3 Cartesian coordinates

The object coordinates are Q-format encoded and must be divided by `tlv_xyzQFormat`:

```python
x_m = x.astype('int16') / tlv_xyzQFormat
y_m = y.astype('int16') / tlv_xyzQFormat
z_m = z.astype('int16') / tlv_xyzQFormat
```

---

## 13. Bugs Encountered and Fixes

### 13.1 `cp210x` confusion

Initial setup assumed a `cp210x` USB-UART bridge, but the board actually enumerated as **CDC-ACM** via XDS110. The correct devices were `ttyACM0` and `ttyACM1`.

### 13.2 `\n` vs `\r\n`

Sending commands with only `\n` caused echo-only behavior. Switching to `\r\n` was required.

### 13.3 udev rule mismatch

Matching `ATTRS{bInterfaceNumber}` directly was unreliable. Matching via `ENV{ID_USB_INTERFACE_NUM}` worked.

### 13.4 SDK mismatch symptoms

The CLI response text helped determine which command syntax the running firmware actually expected.

### 13.5 Crash after bad config

When a partially incompatible config was sent, `sensorStart` caused a runtime exception. A hard power cycle of the board was required before retrying.

### 13.6 Missing frame prints

At one stage, valid frames with zero objects were not displayed because `dataOK` was set only when objects existed. This was changed so that a valid frame header marks the frame as good even when object count is zero.

### 13.7 Integer overflow

Using `int16` too early caused overflows when raw unsigned values such as `65535` were read. The fix was:

- read raw fields as `uint16`
- cast carefully afterward
- use `int32` for Doppler correction arithmetic

---

## 14. Verifying That Data Was Flowing

A raw data diagnostic was used to verify that the data UART was active.

Typical output showed repeated 62-byte and 64-byte chunks and over 6000 bytes received in 5 seconds, confirming that the radar was streaming frames.

This proved that the problem was in parsing or visualization, not in the sensor startup path.

---

## 15. Real-Time Visualization Approach

Because the Raspberry Pi was accessed over SSH with no local display, the visualization was implemented as a **browser-based application**.

### 15.1 Chosen stack

- Python serial reader/parser on the Pi
- Flask web server
- Flask-SocketIO for pushing live frame updates
- Browser-rendered visualization on the laptop

### 15.2 Why browser-based visualization was used

This avoids the need for X11 forwarding, a physical monitor, or GUI libraries running directly on the Pi desktop.

---

## 16. Point-Cloud View vs Radar Map View

Two visualization modes were explored.

### 16.1 Point-cloud style view

A scatter plot used:

- X axis = lateral position (`x`)
- Y axis = forward distance (`range`)

This produces a top-down spatial view of detections.

### 16.2 Radar map / topology-style view

A more radar-like map was implemented with:

- sensor origin at bottom-center
- semicircular range rings
- azimuth guide lines
- moving sweep line
- detection dots placed by range and azimuth
- labels for range and Doppler
- color coding based on velocity

### 16.3 Azimuth estimation

The azimuth angle was approximated as:

```python
azimuth = np.arctan2(x_m, rangeVal)
```

This is sufficient for a practical top-down display even though it is not a full occupancy map.

---

## 17. Radar Map Semantics

The radar map shows:

- **green** for nearly stationary targets
- **red/orange** for targets moving away
- **cyan/blue** for targets moving toward the sensor

Objects are placed according to polar geometry:

- radius = range
- angle = azimuth

---

## 18. Recommended Runtime Workflow

### 18.1 Start the environment

```bash
source ~/radar_env/bin/activate
```

### 18.2 Launch the visualization server

```bash
python3 ~/Radar/radar_visualize.py
```

### 18.3 Open in browser

```text
http://<raspberry-pi-ip>:5000
```

### 18.4 Stop safely

Use `Ctrl+C` so that the script can send `sensorStop` before closing serial ports.

---


