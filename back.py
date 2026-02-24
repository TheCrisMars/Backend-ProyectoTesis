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

CORS(app, resources={r"/*": {"origins": "*"}})

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

NODE_RED_URL = "http://20.57.20.144:1880/pulse"
AUTO_DELAY = 1
HUM_MIN = 41

clients = set()

# --------------------------------------------------------
# BASE DE DATOS
# --------------------------------------------------------
def get_db_connection():
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

NODE_RED_URL = "wss://api.ingeniericast.uk/ws"

def enviar_pulse(accion, pulse, token=None):
    try:
        import websocket
        ws_url = f"{NODE_RED_URL}?token={token}" if token else NODE_RED_URL
        ws = websocket.create_connection(ws_url, timeout=5)
        
        # Formato que espera el servidor WS externo
        payload = {"accion": accion, "pulse": pulse}
        ws.send(json.dumps(payload))
        ws.close()
        print(f"✅ Pulso enviado a VPS: {payload}")
    except Exception as e:
        print(f"⚠️ Error enviando pulso por WS a externa: {e}")

def apagar_pulse_despues(pulse, delay, token=None):
    time.sleep(delay)
    estado_pulsos[pulse] = "off"
    enviar_pulse("off", pulse, token)
    broadcast({"type": "pulses", "data": estado_pulsos})

def escuchar_vps_ws():
    import websocket
    def on_message(ws, message):
        global ultimo_sensado, estado_bomba_actual, estado_pulsos
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "pulses":
                estado_pulsos.update(data.get("data", {}))
                broadcast({"type": "pulses", "data": estado_pulsos})
            
            elif msg_type == "estado_bomba":
                estado_bomba_actual.update(data.get("data", {}))
                broadcast({"type": "estado_bomba", "data": estado_bomba_actual})
                
            elif msg_type == "update_sensor":
                d = data.get("data", {})
                try:
                    humedad = int(d.get("humedad_suelo"))
                except:
                    humedad = None

                ultimo_sensado.update({
                    "humedad_suelo": humedad,
                    "adc": d.get("adc"),
                    "sensor": d.get("sensor"),
                    "origen": d.get("origen"),
                    "finca": d.get("finca")
                })
                broadcast({"type": "nuevo_sensor", "data": ultimo_sensado})
                
            elif msg_type == "estado_entrada":
                # Convertir estado_entrada de la VPS a estado_bomba para el frontend
                estado_bomba_actual.update(data.get("data", {}))
                broadcast({"type": "estado_bomba", "data": estado_bomba_actual})
                
        except Exception as e:
            print("⚠️ Error parseando mensaje de VPS:", e)

    def on_error(ws, error):
        print("⚠️ VPS WS Error:", error)

    def on_close(ws, close_status_code, close_msg):
        print("🔴 VPS WS Desconectado. Reconectando en 5s...")

    def on_open(ws):
        print("🟢 Conectado al WS de la VPS para escuchar eventos")

    while True:
        try:
            ws = websocket.WebSocketApp(NODE_RED_URL,
                                      on_message=on_message,
                                      on_error=on_error,
                                      on_close=on_close,
                                      on_open=on_open)
            ws.run_forever(ping_interval=10, ping_timeout=5)
        except:
            pass
        time.sleep(5)

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

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cur.fetchone()
    cur.close()
    conn.close()

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

@app.route("/api/history", methods=["GET"])
def get_history():
    limit = request.args.get("limit", default=24, type=int)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT created_at, humedad_suelo, adc, sensor, origen, finca 
        FROM sensor_measurements 
        ORDER BY created_at DESC 
        LIMIT %s
    """, (limit,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    history = []
    for r in rows:
        ts = r[0]
        history.append({
            "time": ts.strftime("%H:%M"),
            "date": ts.strftime("%Y-%m-%d"),
            "humedad_suelo": r[1],
            "adc": r[2],
            "sensor": r[3],
            "origen": r[4],
            "finca": r[5]
        })
        
    return jsonify(history)

@app.route("/api/pulse", methods=["POST"])
def control_pulse():
    data = request.get_json()
    accion = data.get("accion")
    pulse = data.get("pulse")

    if accion not in ["on", "off"] or pulse not in estado_pulsos:
        return jsonify({"error": "Datos inválidos"}), 400

    estado_pulsos[pulse] = accion
    broadcast({"type": "pulses", "data": estado_pulsos})
    
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        
    enviar_pulse(accion, pulse, token)

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
    threading.Thread(
        target=escuchar_vps_ws,
        daemon=True
    ).start()

    threading.Thread(
        target=guardar_sensor_periodicamente,
        daemon=True
    ).start()

    port = int(os.environ.get("PORT", 5000))
    server = WebSocketServer(
        ("0.0.0.0", port),
        Resource([
            ("/ws", PulseWS),
            ("/", app)
        ])
    )

    print(f"🚀 Backend activo en 0.0.0.0:{port}")
    server.serve_forever()