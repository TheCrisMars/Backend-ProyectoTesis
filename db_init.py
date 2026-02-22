import os
import psycopg2
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

def init_db():
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
    
    # Insertar usuario de prueba
    username = "hbmingenierias"
    password = "hbm.ingenierias23"
    hashed_password = generate_password_hash(password)
    
    print(f"Insertando usuario '{username}'...")
    try:
        cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
        conn.commit()
        print("✅ Usuario insertado correctamente.")
    except psycopg2.IntegrityError:
        conn.rollback()
        print("⚠️ El usuario ya existe.")

    # Insertar usuario Node_admin
    username2 = "Node_admin"
    password2 = "Node_hbmingenierias26"
    hashed_password2 = generate_password_hash(password2)
    
    print(f"Insertando usuario '{username2}'...")
    try:
        cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username2, hashed_password2))
        conn.commit()
        print("✅ Usuario Node_admin insertado correctamente.")
    except psycopg2.IntegrityError:
        conn.rollback()
        print("⚠️ El usuario Node_admin ya existe.")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    init_db()
