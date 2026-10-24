import sqlite3

DATABASE = "books.db"

conn = sqlite3.connect(DATABASE)

with open("schema.sql", "r", encoding="utf-8") as file:
    conn.executescript(file.read())

# Start with an empty books table so seed data is not duplicated.
conn.execute("DELETE FROM books")
conn.commit()

with open("seed.sql", "r", encoding="utf-8") as file:
    conn.executescript(file.read())

conn.commit()
conn.close()

print("Database created successfully.")
print("12 books were added.")
