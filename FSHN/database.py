import mysql.connector

def get_connection():
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="vihaan2008",
        database="fshn"
    )
    return conn


def init_db():
    conn = mysql.connector.connect(host="localhost", user="root", password="vihaan2008")
    cursor = conn.cursor()
    cursor.execute("CREATE DATABASE IF NOT EXISTS fshn")
    cursor.execute("USE fshn")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(255),
            bodytype VARCHAR(255),
            skincolor VARCHAR(255)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wardrobe (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            type VARCHAR(255),
            color VARCHAR(255),
            cut VARCHAR(255),
            size VARCHAR(255),
            fabric VARCHAR(255),
            availability BOOLEAN,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()


def save_user(name, bodytype, skincolor):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (name, bodytype, skincolor) VALUES (%s, %s, %s)",
        (name, bodytype, skincolor)
    )
    conn.commit()
    new_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return new_id


def save_wardrobe_item(user_id, ctype, color, cut, size, fabric, availability, image_path):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO wardrobe (user_id, type, color, cut, size, fabric, availability, image_path) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, ctype, color, cut, size, fabric, availability, image_path)
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_wardrobe_for_user(user_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM wardrobe WHERE user_id = %s", (user_id,))
    items = cursor.fetchall()
    cursor.close()
    conn.close()
    return items