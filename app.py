from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3

app = Flask(__name__)
app.secret_key = 'pageturner_secret_key'

DATABASE = 'books.db'  # Change to 'database.db' if your filename is database.db

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Create books table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL,
            image_url TEXT NOT NULL
        )
    ''')
    
    # Create orders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            total_amount REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create order_items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (book_id) REFERENCES books (id)
        )
    ''')

    conn.commit()
    conn.close()

# Automatically initialize missing database tables on launch
init_db()

@app.context_processor
def inject_cart_count():
    cart = session.get('cart', {})
    total_count = sum(cart.values())
    return dict(cart_count=total_count)

@app.route('/')
def home():
    conn = get_db()
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    
    query = "SELECT * FROM books WHERE 1=1"
    params = []

    if search:
        query += " AND (title LIKE ? OR author LIKE ? OR category LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    if category:
        query += " AND category = ?"
        params.append(category)

    books = conn.execute(query, params).fetchall()
    categories = [row['category'] for row in conn.execute("SELECT DISTINCT category FROM books").fetchall()]
    conn.close()
    
    return render_template('home.html', books=books, categories=categories)

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    conn = get_db()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    if not book:
        flash("Book not found!", "error")
        return redirect(url_for('home'))
    return render_template('book.html', book=book)

@app.route('/add_to_cart/<int:book_id>', methods=['POST'])
def add_to_cart(book_id):
    cart = session.get('cart', {})
    str_id = str(book_id)
    cart[str_id] = cart.get(str_id, 0) + 1
    session['cart'] = cart
    flash("Book added to cart successfully!", "success")
    return redirect(url_for('cart'))

@app.route('/cart')
def cart():
    cart = session.get('cart', {})
    items = []
    total = 0.0
    conn = get_db()

    for book_id, qty in cart.items():
        book = conn.execute("SELECT * FROM books WHERE id = ?", (int(book_id),)).fetchone()
        if book:
            item_total = book['price'] * qty
            total += item_total
            items.append({'book': book, 'quantity': qty, 'item_total': item_total})

    conn.close()
    return render_template('cart.html', items=items, total=total)

@app.route('/update_cart/<int:book_id>', methods=['POST'])
def update_cart(book_id):
    cart = session.get('cart', {})
    str_id = str(book_id)
    new_qty = int(request.form.get('quantity', 1))

    if new_qty > 0:
        cart[str_id] = new_qty
    else:
        cart.pop(str_id, None)

    session['cart'] = cart
    return redirect(url_for('cart'))

@app.route('/remove_from_cart/<int:book_id>', methods=['POST'])
def remove_from_cart(book_id):
    cart = session.get('cart', {})
    cart.pop(str(book_id), None)
    session['cart'] = cart
    flash("Item removed from cart.", "info")
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    cart = session.get('cart', {})
    if not cart:
        flash("Your cart is empty!", "error")
        return redirect(url_for('home'))

    conn = get_db()

    if request.method == 'POST':
        name = request.form.get('customer_name')
        phone = request.form.get('phone')
        address = request.form.get('address')

        total = 0.0
        order_items_data = []

        for book_id, qty in cart.items():
            book = conn.execute("SELECT * FROM books WHERE id = ?", (int(book_id),)).fetchone()
            if book:
                item_total = book['price'] * qty
                total += item_total
                order_items_data.append((int(book_id), qty, book['price']))

        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO orders (customer_name, phone, address, total_amount) VALUES (?, ?, ?, ?)",
            (name, phone, address, total)
        )
        order_id = cursor.lastrowid

        for item in order_items_data:
            cursor.execute(
                "INSERT INTO order_items (order_id, book_id, quantity, price) VALUES (?, ?, ?, ?)",
                (order_id, item[0], item[1], item[2])
            )

        conn.commit()
        conn.close()

        session.pop('cart', None)
        flash(f"Order #{order_id} placed successfully!", "success")
        return redirect(url_for('orders'))

    items = []
    total = 0.0
    for book_id, qty in cart.items():
        book = conn.execute("SELECT * FROM books WHERE id = ?", (int(book_id),)).fetchone()
        if book:
            item_total = book['price'] * qty
            total += item_total
            items.append({'book': book, 'quantity': qty, 'item_total': item_total})

    conn.close()
    return render_template('checkout.html', items=items, total=total)

@app.route('/orders')
def orders():
    conn = get_db()
    orders_list = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('orders.html', orders=orders_list)

@app.route('/order_confirmation/<int:order_id>')
def order_confirmation(order_id):
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return render_template('orders.html', orders=[order] if order else [])

if __name__ == '__main__':
    app.run(debug=True)