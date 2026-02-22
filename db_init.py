import os
import psycopg2
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def init_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Crear tabla usuarios
    print("Creando tabla users...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL
        );
    """)

    # Crear tabla de mediciones de sensores
    print("Creando tabla sensor_measurements...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_measurements (
            id SERIAL PRIMARY KEY,
            humedad_suelo INTEGER,
            adc VARCHAR(50),
            sensor VARCHAR(50),
            origen VARCHAR(50),
            finca VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Usuarios iniciales (NO pongas contraseñas en el repo).
    # Define en variables de entorno:
    # - INIT_USER_HBM / INIT_PASS_HBM
    # - INIT_USER_NODE_ADMIN / INIT_PASS_NODE_ADMIN
    initial_users = [
        (os.getenv("INIT_USER_HBM", "hbmingenierias"), os.getenv("INIT_PASS_HBM")),
        (os.getenv("INIT_USER_NODE_ADMIN", "Node_admin"), os.getenv("INIT_PASS_NODE_ADMIN")),
    ]

    for username, password in initial_users:
        if not password:
            print(f"ℹ️ Saltando creación de '{username}': falta contraseña en entorno")
            continue

        hashed_password = generate_password_hash(password)

        print(f"Insertando usuario '{username}'...")
        try:
            cur.execute(
                "INSERT INTO users (username, password) VALUES (%s, %s)",
                (username, hashed_password),
            )
            conn.commit()
            print("✅ Usuario insertado correctamente.")
        except psycopg2.IntegrityError:
            conn.rollback()
            print("⚠️ El usuario ya existe.")

    cur.close()
    conn.close()

if __name__ == '__main__':
    init_db()
