# Defect Detection — Host PC Application

Desktop application for industrial surface defect detection, built with PyQt5. Designed to work with the Jetson Orin Nano edge device as a complete inspection system with real-time detection, PLC integration, and production line monitoring.

## Features

- **Real-time detection** — live camera preview with defect bounding boxes, confidence scores, and NG/OK classification
- **Dual inference sources** — TCP inference via Jetson Nano (auto-connect) and local PC inference (ONNX/PT) for offline use
- **Model management** — auto-scan local models, quality scoring, dataset/class identification, one-click model switching
- **PLC integration** — Modbus TCP communication for production line control, automatic NG rejection, and continuous NG alerting
- **History and analytics** — searchable detection records with pagination, trend charts, defect type distribution, and CSV export
- **Camera control** — Hikvision MVS SDK integration with configurable exposure, gain, and ROI visualization
- **Production line simulation** — simulated streaming mode for testing without physical hardware
- **Dark theme UI** — custom frameless window with drag/resize, status bar, and 5-tab layout (Detection / Settings / History / Communication / Logs)

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| Python | 3.10+ (Anaconda recommended) |
| OS | Windows 10/11 |
| Dependencies | PyQt5, OpenCV, onnxruntime, pyserial, matplotlib |

## Quick Start

```bash
# Clone the repository
git clone https://github.com/logicness/defect-detection-host-pc.git
cd defect-detection-host-pc

# Install dependencies
pip install -r requirements.txt

# Launch the application
python main.py
```

## Project Structure

```
├── main.py                           # Application entry point
├── core/
│   ├── controller.py                 # Main controller (TCP/PLC/DB pipeline)
│   ├── tcp_client.py                 # TCP client with heartbeat and auto-reconnect
│   ├── plc_client.py                 # Modbus TCP client (pure socket)
│   ├── local_infer.py                # Local PC inference engine (ONNX/PT)
│   ├── stream_engine.py              # Real-time stream engine
│   ├── database.py                   # SQLite storage and analytics
│   └── config.py                     # Configuration persistence
├── components/
│   ├── image_preview.py              # Preview with zoom, pan, ROI, and defect overlays
│   ├── model_manager_dialog.py       # Model selection and management UI
│   ├── roi_editor.py                 # Visual ROI editor with drag/resize
│   └── stats_charts.py              # Trend and distribution charts
├── pages/                            # 5 tab pages (Detection, Settings, History, Comm, Logs)
├── assets/qss/dark_theme.qss         # Dark theme stylesheet
├── data/                             # Runtime data (auto-created)
└── requirements.txt
```

> **Not included:** Test suites, demo data generators, PLC simulator, and PyInstaller packaging config are available in the private companion repository.

## Communication Protocol

**TCP (Nano inference):** 4-byte big-endian length header + UTF-8 JSON.
Message types: `detect_request` / `detect_response` / `heartbeat` / `control` / `model_list_request` / `model_load_request` / `stream_frame` and more.

**Modbus TCP (PLC):** MBAP header + PDU. Function codes 0x01/0x03/0x05.
Address mapping: Coil 0=line run, 1=fault, 2=reject trigger (write), 3=reject confirm; Register 0=OK count, 1=NG count.

## Configuration

Default connection parameters (replace with your actual addresses):

| Parameter | Default | Description |
|-----------|---------|-------------|
| Nano host | `[NANO_LAN_IP]:8888` | Jetson device TCP endpoint |
| PLC host | `[PLC_LAN_IP]:2000` | Modbus TCP endpoint |

The application does not auto-connect to the Nano on startup. Click "Reconnect" on the Detection or Model Management page to initiate the connection.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
