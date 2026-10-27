"""Apply numbered SQL migrations once, in filename order, using SQLite transactions."""
from pathlib import Path
import sqlite3

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database.db"
MIGRATIONS = BASE_DIR / "migrations"

def main():
    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            if conn.execute("SELECT 1 FROM schema_version WHERE version = ?", (path.name,)).fetchone():
                continue
            script = path.read_text(encoding="utf-8")
            try:
                conn.executescript("BEGIN IMMEDIATE;\n" + script + "\nINSERT INTO schema_version(version) VALUES (" + "'" + path.name.replace("'", "''") + "'" + ");\nCOMMIT;")
            except sqlite3.Error:
                conn.rollback()
                raise
            print(f"Applied {path.name}")
        print("Migrations are up to date.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
