# IWR1443BOOST + Raspberry Pi 3 Model B+ Setup Guide

## Overview

This guide documents the complete setup process for interfacing the **TI IWR1443BOOST mmWave radar** with a **Raspberry Pi 3 Model B+** over USB serial, as a precursor to streaming radar data to the cloud.

---

## System Architecture

```
IWR1443BOOST  -->  USB (XDS110 / CDC-ACM)  -->  Raspberry Pi 3B+  -->  Cloud (MQTT)
  mmWave Radar        /dev/iwr_cli (CLI)         Python Parser         HiveMQ / AWS IoT
                      /dev/iwr_data (Data)
```

---

## Hardware

| Component | Details |
|---|---|
| Radar Sensor | TI IWR1443BOOST |
| Host SBC | Raspberry Pi 3 Model B+ |
| USB Bridge | Onboard XDS110 (CDC-ACM driver) |
| OS | Raspberry Pi OS (Kernel 6.12.47+rpt-rpi-v7) |
| Connection | SSH from laptop |

---

## Prerequisites

- Raspberry Pi 3B+ running Raspberry Pi OS
- SSH access to the Pi from your laptop
- IWR1443BOOST with a **data-capable** micro-USB cable
- Internet access on the Pi

---

## Step 1 — System Update

```bash
sudo apt update && sudo apt upgrade -y
```

---

## Step 2 — USB Driver Verification

The IWR1443BOOST uses the onboard **XDS110** debug probe, which enumerates via the **`cdc_acm`** kernel driver (not `cp210x`). This driver is built into the Raspberry Pi OS kernel — no manual installation needed.

Plug in the IWR1443BOOST and verify detection:

```bash
lsusb | grep -i "0451"
dmesg | grep -i "acm"
```

Expected `lsusb` entry:
```
Bus 001 Device 005: ID 0451:bef3 Texas Instruments, Inc. XDS110...
```

Expected `dmesg` entries:
```
cdc_acm 1-1.1.2:1.0: ttyACM0: USB ACM device
cdc_acm 1-1.1.2:1.3: ttyACM1: USB ACM device
```

Verify the ports exist:
```bash
ls /dev/ttyACM*
# /dev/ttyACM0   /dev/ttyACM1
```

### Port Mapping

| Device | Function | Baud Rate |
|---|---|---|
| `/dev/ttyACM0` | CLI Port (send radar config commands) | 115200 |
| `/dev/ttyACM1` | Data Port (receive binary TLV frames) | 921600 |

---

## Step 3 — Add User to `dialout` Group

```bash
sudo usermod -aG dialout $USER
# Log out and log back in via SSH
exit
# Reconnect, then verify:
groups   # 'dialout' should appear in the list
```

---

## Step 4 — Python Virtual Environment

```bash
sudo apt install python3-pip python3-venv git -y

python3 -m venv ~/radar_env
source ~/radar_env/bin/activate

pip install pyserial numpy paho-mqtt
```

> **Note:** Activate the environment every session with `source ~/radar_env/bin/activate`.

---

## Step 5 — Persistent USB Port Names (udev Rules)

By default, port assignment (`ttyACM0` / `ttyACM1`) can swap on reboot. udev rules create fixed symlinks `/dev/iwr_cli` and `/dev/iwr_data`.

### Device Attributes (confirmed via `udevadm info`)

| Attribute | Value |
|---|---|
| `ID_VENDOR_ID` | `0451` |
| `ID_MODEL_ID` | `bef3` |
| `ID_USB_INTERFACE_NUM` (CLI) | `00` |
| `ID_USB_INTERFACE_NUM` (Data) | `03` |

### Create the Rules File

```bash
sudo nano /etc/udev/rules.d/99-iwr1443.rules
```

Paste:

```
# IWR1443 CLI Port
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="0451", ENV{ID_MODEL_ID}=="bef3", ENV{ID_USB_INTERFACE_NUM}=="00", SYMLINK+="iwr_cli", GROUP="dialout", MODE="0666"

# IWR1443 Data Port
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="0451", ENV{ID_MODEL_ID}=="bef3", ENV{ID_USB_INTERFACE_NUM}=="03", SYMLINK+="iwr_data", GROUP="dialout", MODE="0666"
```


### Apply Rules

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
ls -l /dev/iwr*
```

Expected output:
```
lrwxrwxrwx 1 root root 7 ... /dev/iwr_cli  -> ttyACM0
lrwxrwxrwx 1 root root 7 ... /dev/iwr_data -> ttyACM1
```



## Step 6 — Verify Serial Port Access

```bash
source ~/radar_env/bin/activate

python3 -c "
import serial
cli  = serial.Serial('/dev/iwr_cli',  115200, timeout=1)
data = serial.Serial('/dev/iwr_data', 921600, timeout=1)
print('CLI  port OK:', cli.name)
print('Data port OK:', data.name)
cli.close()
data.close()
"
```

Expected output:
```
CLI  port OK: /dev/iwr_cli
Data port OK: /dev/iwr_data
```

---



## Setup Checklist

- [x] System updated
- [x] XDS110 USB device detected (`0451:bef3`)
- [x] `cdc_acm` driver active — ports at `/dev/ttyACM0` and `/dev/ttyACM1`
- [x] User added to `dialout` group
- [x] Python virtual environment created with `pyserial`, `numpy`, `paho-mqtt`
- [x] udev rules created with `ENV{ID_USB_INTERFACE_NUM}` matching
- [x] Persistent symlinks `/dev/iwr_cli` and `/dev/iwr_data` verified
- [x] Both serial ports opened successfully from Python

