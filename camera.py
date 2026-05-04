#!/usr/bin/env python3
"""
Patient Fall Detection Monitor
Raspberry Pi 5 - Local Network Only
"""

import cv2
import threading
import time
import base64
import json
import os
import requests
import logging
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, Response, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit

# ── Configuration ──────────────────────────────────────────────────────────────
PASSWORD = os.environ.get("MONITOR_PASSWORD", "geheim123")
SECRET_KEY = os.environ.get("SECRET_KEY", "dein-geheimer-schluessel-aendern!")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_CHECK_INTERVAL = 5       # seconds between AI analysis
ALERT_COOLDOWN = 30         # seconds between repeated alerts
CAMERA_INDEX = 0            # /dev/video0

# ── App Setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── Camera ─────────────────────────────────────────────────────────────────────
class Camera:
    def __init__(self):
        self.cap = None
        self.frame = None
        self.lock = threading.Lock()
        self.running = False
        self._init_camera()

    def _init_camera(self):
        try:
            self.cap = cv2.VideoCapture(CAMERA_INDEX)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            if not self.cap.isOpened():
                raise RuntimeError("Kamera nicht gefunden")
            self.running = True
            threading.Thread(target=self._capture_loop, daemon=True).start()
            log.info("Kamera gestartet")
        except Exception as e:
            log.error(f"Kamera Fehler: {e}")
            self.running = False

    def _capture_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.frame = frame
            else:
                time.sleep(0.1)

    def get_frame(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def get_jpeg(self):
        frame = self.get_frame()
        if frame is None:
            return None
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def get_base64(self):
        jpeg = self.get_jpeg()
        if jpeg is None:
            return None
        return base64.b64encode(jpeg).decode('utf-8')

camera = Camera()

# ── Alert State ────────────────────────────────────────────────────────────────
class AlertManager:
    def __init__(self):
        self.alerts = []
        self.last_alert_time = 0
        self.status = "monitoring"   # monitoring | warning | alert
        self.last_analysis = "Überwachung läuft..."
        self.lock = threading.Lock()

    def add_alert(self, message, level="warning"):
        now = time.time()
        with self.lock:
            if now - self.last_alert_time < ALERT_COOLDOWN and level != "critical":
                return
            self.last_alert_time = now
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": message,
                "level": level
            }
            self.alerts.insert(0, entry)
            self.alerts = self.alerts[:50]   # keep last 50
            self.status = level
        # Emit via websocket
        socketio.emit('alert', entry)
        log.warning(f"ALERT [{level}]: {message}")

alerts = AlertManager()

# ── AI Monitoring ──────────────────────────────────────────────────────────────
def analyze_frame_with_claude(b64_image):
    """Send frame to Claude claude-sonnet-4-20250514 for fall detection."""
    if not ANTHROPIC_API_KEY:
        return None, "Kein API-Key konfiguriert"

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_image
                            }
                        },
                        {
                            "type": "text",
                            "text": """Du überwachst einen Patienten in einem Krankenbett/Pflegebett.
Analysiere das Bild und antworte NUR mit einem JSON-Objekt:
{
  "status": "ok" | "warning" | "critical",
  "beschreibung": "kurze Beschreibung was du siehst",
  "aktion": null | "Empfohlene sofortige Maßnahme"
}

Regeln:
- "ok": Patient liegt ruhig im Bett oder ist nicht zu sehen
- "warning": Patient bewegt sich stark, sitzt aufrecht am Bettrand, oder ungewöhnliche Position
- "critical": Patient liegt auf dem Boden oder ist aus dem Bett gefallen

Antworte NUR mit dem JSON, kein weiterer Text."""
                        }
                    ]
                }]
            },
            timeout=10
        )

        if response.status_code == 200:
            text = response.json()["content"][0]["text"].strip()
            # Clean possible markdown fences
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            return data, None
        else:
            return None, f"API Fehler: {response.status_code}"

    except json.JSONDecodeError as e:
        return None, f"JSON Parse Fehler: {e}"
    except Exception as e:
        return None, f"Anfrage Fehler: {e}"


def ai_monitor_loop():
    """Background thread: periodically analyses camera frame."""
    log.info("KI-Überwachung gestartet")
    while True:
        time.sleep(AI_CHECK_INTERVAL)
        if not camera.running:
            continue

        b64 = camera.get_base64()
        if b64 is None:
            continue

        result, error = analyze_frame_with_claude(b64)

        if error:
            log.error(f"AI Fehler: {error}")
            with alerts.lock:
                alerts.last_analysis = f"KI Fehler: {error}"
            socketio.emit('status_update', {'analysis': f"⚠ {error}", 'status': alerts.status})
            continue

        with alerts.lock:
            alerts.last_analysis = result.get("beschreibung", "")

        status = result.get("status", "ok")
        beschreibung = result.get("beschreibung", "")
        aktion = result.get("aktion")

        socketio.emit('status_update', {
            'analysis': beschreibung,
            'status': status,
            'aktion': aktion
        })

        if status == "critical":
            msg = f"⚠️ STURZ ERKANNT: {beschreibung}"
            if aktion:
                msg += f" → {aktion}"
            alerts.add_alert(msg, "critical")
        elif status == "warning":
            msg = f"Achtung: {beschreibung}"
            alerts.add_alert(msg, "warning")
        else:
            with alerts.lock:
                alerts.status = "monitoring"


# ── Auth ───────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == PASSWORD:
            session['authenticated'] = True
            session.permanent = True
            return redirect(url_for('index'))
        error = "Falsches Passwort"
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/video_feed')
@login_required
def video_feed():
    def generate():
        while True:
            jpeg = camera.get_jpeg()
            if jpeg:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n')
            time.sleep(0.033)   # ~30fps
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/alerts')
@login_required
def get_alerts():
    with alerts.lock:
        return jsonify({
            'alerts': alerts.alerts,
            'status': alerts.status,
            'analysis': alerts.last_analysis
        })

@app.route('/api/snapshot')
@login_required
def snapshot():
    b64 = camera.get_base64()
    if b64:
        return jsonify({'image': b64})
    return jsonify({'error': 'Kein Bild verfügbar'}), 503

# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if ANTHROPIC_API_KEY:
        threading.Thread(target=ai_monitor_loop, daemon=True).start()
    else:
        log.warning("ANTHROPIC_API_KEY nicht gesetzt — KI-Überwachung deaktiviert")

    # Bind only to local network (not 0.0.0.0 would allow all, but we use firewall/wlan)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)


