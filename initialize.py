import hashlib
import sqlite3
from app import DB_PATH, init_db


def reset_db():
    """Erase all data and create default admin user."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    tables = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    for (table,) in tables:
        cur.execute(f"DELETE FROM {table}")

    password = hashlib.sha256("admin".encode()).hexdigest()
    cur.execute(
        "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
        ("Admin", "admin@email.com", password),
    )
    conn.commit()
    conn.close()
    print("Database cleaned and default user created.")


if __name__ == "__main__":
    reset_db()
