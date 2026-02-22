import threading
import time
import json
import os
import psycopg2
import jwt
import datetime
import requests

from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from werkzeug.security import check_password_hash
from functools import wraps
from dotenv import load_dotenv

from geventwebsocket import WebSocketServer, WebSocketApplication, Resource

# --------------------------------------------------------
# CONFIGURACIÓN INICIAL
# --------------------------------------------------------
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "super_secret_key")
DATABASE_URL = os.getenv("DATABASE_URL")

# Render (y la mayoría de PaaS) inyecta PORT; local puede usar 5000.
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

CORS(app, resources={r"/*": {"origins": "*"}})


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200

# --------------------------------------------------------
# ESTADOS GLOBALES
# --------------------------------------------------------
estado_pulsos = {1: "off", 2: "off", 3: "off", 4: "off"}

estado_bomba_actual = {
    "marca": None,
    "estado": None,
    "timestamp": None
}

ultimo_sensado = {
    "humedad_suelo": None,
    "adc": None,
    "sensor": None,
    "origen": None,
    "finca": None
}

NODE_RED_URL = os.getenv("NODE_RED_URL", "http://20.57.20.144:1880/pulse")
AUTO_DELAY = 1
HUM_MIN = 41

clients = set()

# --------------------------------------------------------
# BASE DE DATOS
# --------------------------------------------------------
def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada")
    return psycopg2.connect(DATABASE_URL)

# --------------------------------------------------------
# JWT DECORATOR (SOLO REST)
# --------------------------------------------------------
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        if "Authorization" in request.headers:
            h = request.headers["Authorization"]
            if h.startswith("Bearer "):
                token = h.split(" ")[1]

        if not token:
            return jsonify({"message": "Token faltante"}), 401

        try:
            jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        except Exception:
            return jsonify({"message": "Token inválido"}), 401

        return f(*args, **kwargs)

    return decorated

# --------------------------------------------------------
# FUNCIONES AUXILIARES
# --------------------------------------------------------
def broadcast(message):
    dead = []
    for ws in clients:
        try:
            ws.send(json.dumps(message))
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)

def enviar_pulse(accion, pulse):
    try:
        requests.post(
            NODE_RED_URL,
            json={"accion": accion, "pulse": pulse},
            timeout=3
        )
    except Exception:
        pass

def apagar_pulse_despues(pulse, delay):
    time.sleep(delay)
    estado_pulsos[pulse] = "off"
    enviar_pulse("off", pulse)
    broadcast({"type": "pulses", "data": estado_pulsos})

# --------------------------------------------------------
# GUARDAR SENSOR CADA 1 HORA
# --------------------------------------------------------
def guardar_sensor_periodicamente():
    while True:
        try:
            time.sleep(3600)  # ⏱️ 1 hora (usa 30 para pruebas)

            if ultimo_sensado["humedad_suelo"] is None:
                print("⚠️ No hay datos de sensor aún")
                continue

            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO sensor_measurements
                (humedad_suelo, adc, sensor, origen, finca)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                ultimo_sensado["humedad_suelo"],
                ultimo_sensado["adc"],
                ultimo_sensado["sensor"],
                ultimo_sensado["origen"],
                ultimo_sensado["finca"]
            ))

            conn.commit()
            cur.close()
            conn.close()

            print("💾 Sensor guardado:", ultimo_sensado)

        except Exception as e:
            print("❌ Error DB:", e)

# --------------------------------------------------------
# LOGIN
# --------------------------------------------------------
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return make_response("Credenciales requeridas", 401)

    username = data.get("username")
    password = data.get("password")

    if not DATABASE_URL:
        return make_response("Servidor sin base de datos configurada", 500)

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
    except Exception:
        return make_response("Error de conexión a la base de datos", 500)

    if not user or not check_password_hash(user[2], password):
        return make_response("Credenciales inválidas", 401)

    token = jwt.encode(
        {
            "user": username,
            "role": "user",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        },
        app.config["SECRET_KEY"],
        algorithm="HS256"
    )

    return jsonify({"token": token})

# --------------------------------------------------------
# API REST
# --------------------------------------------------------
@app.route("/api/status", methods=["GET"])
@token_required
def api_status():
    return jsonify({
        "pulses": estado_pulsos,
        "sensor": ultimo_sensado,
        "bomba": estado_bomba_actual
    })

@app.route("/api/pulse", methods=["POST"])
@token_required
def control_pulse():
    data = request.get_json()
    accion = data.get("accion")
    pulse = data.get("pulse")

    if accion not in ["on", "off"] or pulse not in estado_pulsos:
        return jsonify({"error": "Datos inválidos"}), 400

    estado_pulsos[pulse] = accion
    broadcast({"type": "pulses", "data": estado_pulsos})
    enviar_pulse(accion, pulse)

    if accion == "on":
        threading.Thread(
            target=apagar_pulse_despues,
            args=(pulse, AUTO_DELAY),
            daemon=True
        ).start()

    return jsonify({"estado": "ok"})

# --------------------------------------------------------
# WEBSOCKET (SIN TOKEN)
# --------------------------------------------------------
class PulseWS(WebSocketApplication):

    def on_open(self):
        clients.add(self.ws)
        print("🟢 WS conectado (sin token)")

        self.ws.send(json.dumps({"type": "pulses", "data": estado_pulsos}))
        self.ws.send(json.dumps({"type": "estado_bomba", "data": estado_bomba_actual}))
        self.ws.send(json.dumps({"type": "nuevo_sensor", "data": ultimo_sensado}))

    def on_message(self, message):
        global ultimo_sensado, estado_bomba_actual

        if not message:
            return

        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "update_sensor":
                d = data.get("data", {})

                try:
                    humedad = int(d.get("humedad_suelo"))
                except:
                    humedad = None

                ultimo_sensado = {
                    "humedad_suelo": humedad,
                    "adc": d.get("adc"),
                    "sensor": d.get("sensor"),
                    "origen": d.get("origen"),
                    "finca": d.get("finca")
                }

                broadcast({"type": "nuevo_sensor", "data": ultimo_sensado})

                if humedad is not None and humedad < HUM_MIN:
                    estado_pulsos[4] = "on"
                    broadcast({"type": "pulses", "data": estado_pulsos})
                    enviar_pulse("on", 4)

                    threading.Thread(
                        target=apagar_pulse_despues,
                        args=(4, AUTO_DELAY),
                        daemon=True
                    ).start()

            elif msg_type == "estado_bomba":
                estado_bomba_actual.update(data.get("data", {}))
                broadcast({"type": "estado_bomba", "data": estado_bomba_actual})

        except Exception as e:
            print("⚠️ WS error:", e)

    def on_close(self, reason):
        clients.discard(self.ws)
        print("🔴 WS desconectado")

# --------------------------------------------------------
# MAIN
# --------------------------------------------------------
if __name__ == "__main__":
    if DATABASE_URL:
        threading.Thread(
            target=guardar_sensor_periodicamente,
            daemon=True
        ).start()
    else:
        print("⚠️ DATABASE_URL no configurada: sin guardado periódico")

    server = WebSocketServer(
        (HOST, PORT),
        Resource([
            ("/ws", PulseWS),
            ("/", app)
        ])
    )

    print(f"🚀 Backend activo en {HOST}:{PORT}")
    server.serve_forever()
