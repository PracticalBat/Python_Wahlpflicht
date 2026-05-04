#!/usr/bin/env python3
"""
Raspberry Pi 5 – Kamera-Livestream im Browser
Abhängigkeiten:  pip install flask picamera2
Starten:         python3 camera_stream.py
Browser:         http://<raspi-ip>:5000
"""

import io
import threading
import time
from flask import Flask, Response, render_template_string

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    PICAMERA_AVAILABLE = True
except ImportError:
    PICAMERA_AVAILABLE = False
    print("⚠  picamera2 nicht gefunden – Demo-Modus aktiv (kein echtes Bild).")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
HOST        = "0.0.0.0"
PORT        = 5000
RESOLUTION  = (1280, 720)   # Breite × Höhe
FRAMERATE   = 30            # Frames pro Sekunde

# ---------------------------------------------------------------------------
# Streaming-Klassen
# ---------------------------------------------------------------------------
class StreamOutput(io.BufferedIOBase):
    """Puffer für den aktuellen JPEG-Frame."""
    def __init__(self):
        self.frame: bytes = b""
        self.condition = threading.Condition()

    def write(self, buf: bytes) -> int:
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
        return len(buf)


class Camera:
    """Verwaltet die Picamera2-Instanz und den JPEG-Stream."""
    def __init__(self):
        self.output = StreamOutput()
        if not PICAMERA_AVAILABLE:
            return

        self.cam = Picamera2()
        config = self.cam.create_video_configuration(
            main={"size": RESOLUTION},
            controls={"FrameRate": FRAMERATE},
        )
        self.cam.configure(config)
        self.cam.start_recording(JpegEncoder(), FileOutput(self.output))
        print(f"✅  Kamera gestartet – {RESOLUTION[0]}×{RESOLUTION[1]} @ {FRAMERATE} fps")

    def get_frame(self) -> bytes:
        if not PICAMERA_AVAILABLE:
            # Demo: leeres graues JPEG zurückgeben
            return self._dummy_frame()
        with self.output.condition:
            self.output.condition.wait()
            return self.output.frame

    @staticmethod
    def _dummy_frame() -> bytes:
        """Minimales 1×1-graues JPEG als Platzhalter."""
        import struct, zlib
        # Winziges 1×1 graues JPEG
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
            b"\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
            b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5"
            b"\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01"
            b"}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91"
            b"\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%"
            b"&'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87"
            b"\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5"
            b"\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3"
            b"\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda"
            b"\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6"
            b"\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd4P\x00"
            b"\x00\x00\x1f\xff\xd9"
        )

    def stop(self):
        if PICAMERA_AVAILABLE:
            self.cam.stop_recording()


# ---------------------------------------------------------------------------
# Flask-App
# ---------------------------------------------------------------------------
app = Flask(__name__)
camera = Camera()

HTML_PAGE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>RPi 5 – Kameralive</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: #0a0a0f;
      color: #e0e0e0;
      font-family: 'Courier New', monospace;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
      padding: 2rem 1rem;
    }

    header {
      text-align: center;
      margin-bottom: 1.5rem;
    }
    header h1 {
      font-size: 1.4rem;
      letter-spacing: .2em;
      text-transform: uppercase;
      color: #7ef7a0;
      text-shadow: 0 0 12px #3cff7080;
    }
    header p {
      font-size: .75rem;
      color: #555;
      margin-top: .35rem;
      letter-spacing: .1em;
    }

    .frame {
      position: relative;
      border: 1px solid #1e4d2b;
      border-radius: 4px;
      overflow: hidden;
      box-shadow: 0 0 40px #00ff4415, 0 0 0 1px #0f2a14;
      max-width: 900px;
      width: 100%;
    }
    .frame img {
      display: block;
      width: 100%;
      height: auto;
    }

    /* Scan-line overlay */
    .frame::after {
      content: '';
      position: absolute;
      inset: 0;
      background: repeating-linear-gradient(
        to bottom,
        transparent 0px,
        transparent 3px,
        rgba(0,0,0,.18) 3px,
        rgba(0,0,0,.18) 4px
      );
      pointer-events: none;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: .4rem;
      margin-top: 1rem;
      padding: .3rem .7rem;
      border: 1px solid #1e4d2b;
      border-radius: 2px;
      font-size: .7rem;
      letter-spacing: .12em;
      color: #7ef7a0;
    }
    .dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: #3cff70;
      box-shadow: 0 0 6px #3cff70;
      animation: pulse 1.4s ease-in-out infinite;
    }
    @keyframes pulse {
      0%,100% { opacity: 1; }
      50%      { opacity: .3; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Raspberry Pi&nbsp;5 &mdash; Live Camera</h1>
    <p>MJPEG · {{ res }} · {{ fps }}&thinsp;fps</p>
  </header>

  <div class="frame">
    <img src="/stream" alt="Live-Kamerabild" />
  </div>

  <div class="badge">
    <span class="dot"></span> LIVE
  </div>
</body>
</html>"""


def generate_frames():
    """MJPEG-Generator: liefert Frame für Frame an den Browser."""
    while True:
        frame = camera.get_frame()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame +
            b"\r\n"
        )


@app.route("/")
def index():
    return render_template_string(
        HTML_PAGE,
        res=f"{RESOLUTION[0]}×{RESOLUTION[1]}",
        fps=FRAMERATE,
    )


@app.route("/stream")
def stream():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print(f"🌐  Server läuft → http://{ip}:{PORT}")
    try:
        app.run(host=HOST, port=PORT, threaded=True)
    finally:
        camera.stop()
