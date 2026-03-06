# IWR1443 + Raspberry Pi 3B+ Setup Documentation

**Project:** mmWave Radar Data Pipeline  
**Hardware:** TI IWR1443BOOST + Raspberry Pi 3 Model B+  
**OS:** Raspbian (Kernel 6.12.47+rpt-rpi-v7)  
**Date:** March 6, 2026  
**Author:** Saikishen Pv

---

## 1. System Architecture

```
IWR1443BOOST
  ├── USB (XDS110 / CDC-ACM)
  │     ├── ttyACM0 → /dev/iwr_cli  (CLI Port,  115200 baud)
  │     └── ttyACM1 → /dev/iwr_data (Data Port, 921600 baud)
  │
Raspberry Pi 3B+ (Raspbian)
  ├── USB Host
  ├── Python 3 Virtual Environment (radar_env)
  │     ├── pyserial
  │     ├── numpy
  │     └── paho-mqtt
  │
Cloud
  └── MQTT Broker (HiveMQ / AWS IoT Core)
```

---

## 2. IWR1443 USB Identification

The IWR1443BOOST uses the **XDS110 onboard debug probe** as its USB bridge.  
On Linux, the XDS110 uses the **`cdc_acm`** driver (NOT `cp210x`), and enumerates as `/dev/ttyACM*`.

| Property         | Value                          |
|------------------|-------------------------------|
| USB Vendor ID    | `0451` (Texas Instruments)     |
| USB Product ID   | `bef3`                         |
| Driver           | `cdc_acm`                      |
| CLI Interface    | `bInterfaceNumber == 00`        |
| Data Interface   | `bInterfaceNumber == 03`        |
| CLI Device       | `/dev/ttyACM0`                  |
| Data Device      | `/dev/ttyACM1`                  |

Confirmed via:
```bash
dmesg | grep -i "acm|xds|0451"
# Output:
# cdc_acm 1-1.1.2:1.0: ttyACM0: USB ACM device
# cdc_acm 1-1.1.2:1.3: ttyACM1: USB ACM device
```

---

## 3. Raspberry Pi Setup Steps

### 3.1 System Update
```bash
sudo apt update && sudo apt upgrade -y
```

### 3.2 Add User to `dialout` Group
Required for non-root serial port access:
```bash
sudo usermod -aG dialout $USER
# Log out and back in, then verify:
groups   # should include 'dialout'
```

### 3.3 Install Python Dependencies
```bash
sudo apt install python3-pip python3-venv git -y

python3 -m venv ~/radar_env
source ~/radar_env/bin/activate

pip install pyserial numpy paho-mqtt
```

Activate environment for every session:
```bash
source ~/radar_env/bin/activate
```

---

## 4. Persistent USB Port Names (udev Rules)

Without udev rules, port assignments (`ttyACM0`/`ttyACM1`) can swap on reboot.

### Key Lesson Learned
`ATTRS{bInterfaceNumber}` and `ATTRS{idVendor}` exist at **different levels** of the USB device tree.  
Mixing them in one rule causes a silent match failure.  
The correct approach is to use **`ENV{}`** variables instead.

### Working Rule File
**Path:** `/etc/udev/rules.d/99-iwr1443.rules`

```
# IWR1443 CLI Port (ttyACM0)
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="0451", ENV{ID_MODEL_ID}=="bef3", ENV{ID_USB_INTERFACE_NUM}=="00", SYMLINK+="iwr_cli", GROUP="dialout", MODE="0666"

# IWR1443 Data Port (ttyACM1)
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="0451", ENV{ID_MODEL_ID}=="bef3", ENV{ID_USB_INTERFACE_NUM}=="03", SYMLINK+="iwr_data", GROUP="dialout", MODE="0666"
```

### Apply Rules
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
# or unplug and replug the USB cable
```

### Verify Rules (Dry-Run)
```bash
sudo udevadm test $(udevadm info -q path -n /dev/ttyACM0) 2>&1 | grep -E "iwr|SYMLINK|LINK"
# Expected: ttyACM0: ... LINK 'iwr_cli'
#           ttyACM0: Successfully created symlink '/dev/iwr_cli' to '/dev/ttyACM0'
```

### Verify Symlinks
```bash
ls -l /dev/iwr*
# lrwxrwxrwx ... /dev/iwr_cli  -> ttyACM0
# lrwxrwxrwx ... /dev/iwr_data -> ttyACM1
```

---

## 5. Final Verification

```python
import serial

cli  = serial.Serial('/dev/iwr_cli',  115200, timeout=1)
data = serial.Serial('/dev/iwr_data', 921600, timeout=1)
print('CLI  port OK:', cli.name)
print('Data port OK:', data.name)
cli.close()
data.close()

# Output:
# CLI  port OK: /dev/iwr_cli
# Data port OK: /dev/iwr_data
```

---

## 6. Troubleshooting Reference

| Issue | Cause | Fix |
|-------|-------|-----|
| `/dev/ttyUSB*` not found | XDS110 uses `cdc_acm`, not `cp210x` | Look for `/dev/ttyACM*` instead |
| `modprobe cp210x` fails | Driver is built into kernel (`=y`) | Skip modprobe; check `dmesg` instead |
| udev symlinks not created | `ATTRS{}` cross-level matching failure | Use `ENV{ID_VENDOR_ID}` and `ENV{ID_USB_INTERFACE_NUM}` |
| Symlinks missing after reload | udev doesn't re-trigger live ACM devices | Unplug and replug USB cable |
| Permission denied on serial | User not in `dialout` group | `sudo usermod -aG dialout $USER` then re-login |

---

## 7. Next Steps

- [ ] Clone IWR1443 Python TLV parser
- [ ] Send radar config (`.cfg` file) via CLI port
- [ ] Read and decode binary TLV frames from Data port
- [ ] Publish parsed point cloud data to MQTT broker
- [ ] Visualize on cloud dashboard

---
*Setup completed and verified on Raspberry Pi 3B+ — March 6, 2026*
