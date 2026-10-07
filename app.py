import sqlite3
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, flash

app = Flask(__name__)
app.secret_key = 'super_secret_pageturner_key'
DATABASE = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Create tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    # Safely add 'name' column if an older users table is missing it (keeps all existing data!)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            price REAL NOT NULL CHECK(price >= 0),
            rating REAL DEFAULT 4.5 CHECK(rating >= 0 AND rating <= 5),
            stock INTEGER DEFAULT 10 CHECK(stock >= 0),
            category TEXT NOT NULL,
            image_url TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            book_title TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
            comment TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            customer_address TEXT,
            total_amount REAL NOT NULL CHECK(total_amount >= 0),
            discount_amount REAL DEFAULT 0.0,
            status TEXT DEFAULT 'Placed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Add missing order columns safely if upgrading existing database
    new_columns = [
        ("customer_name", "TEXT"),
        ("customer_email", "TEXT"),
        ("customer_phone", "TEXT"),
        ("customer_address", "TEXT"),
        ("discount_amount", "REAL DEFAULT 0.0")
    ]
    
    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    # Seed books if empty
    cursor.execute('SELECT COUNT(*) FROM books')
    if cursor.fetchone()[0] == 0:
        sample_books = [
            ("The Great Gatsby", "F. Scott Fitzgerald", 499.00, 4.8, 15, "Fiction", "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400"),
            ("To Kill a Mockingbird", "Harper Lee", 550.00, 4.9, 12, "Fiction", "https://images.unsplash.com/photo-1512820790803-83ca734da794?w=400"),
            ("1984", "George Orwell", 399.00, 4.7, 8, "Sci-Fi", "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?w=400"),
            ("Atomic Habits", "James Clear", 650.00, 5.0, 20, "Self-Help", "https://images.unsplash.com/photo-1589829085413-56de8ae18c73?w=400"),
            ("The Hobbit", "J.R.R. Tolkien", 599.00, 4.8, 10, "Fantasy", "https://images.unsplash.com/photo-1629992101753-56d196c8aced?w=400"),
            ("Sapiens", "Yuval Noah Harari", 750.00, 4.6, 14, "History", "https://images.unsplash.com/photo-1497633762265-9d179a990aa6?w=400"),
            ("Clean Code", "Robert C. Martin", 1250.00, 4.9, 6, "Technology", "https://images.unsplash.com/photo-1532012197267-da84d127e765?w=400"),
            ("Deep Work", "Cal Newport", 499.00, 4.7, 11, "Self-Help", "https://images.unsplash.com/photo-1524995997946-a1c2e315a42f?w=400")
        ]
        cursor.executemany('''
            INSERT INTO books (title, author, price, rating, stock, category, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', sample_books)

    conn.commit()
    conn.close()

# Home Route
@app.route('/')
def home():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    sort = request.args.get('sort', '').strip()
    
    try:
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
    except ValueError:
        page = 1
    
    per_page = 8

    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM books WHERE 1=1"
    params = []

    if q:
        query += " AND (title LIKE ? OR author LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])

    if category:
        query += " AND category = ?"
        params.append(category)

    count_query = f"SELECT COUNT(*) FROM ({query})"
    cursor.execute(count_query, params)
    total_books = cursor.fetchone()[0]
    total_pages = (total_books + per_page - 1) // per_page if total_books > 0 else 1

    if page > total_pages: page = total_pages

    if sort == 'price_asc': query += " ORDER BY price ASC"
    elif sort == 'price_desc': query += " ORDER BY price DESC"
    elif sort == 'rating': query += " ORDER BY rating DESC"
    else: query += " ORDER BY id DESC"

    offset = (page - 1) * per_page
    query += " LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    cursor.execute(query, params)
    books = cursor.fetchall()

    cursor.execute("SELECT DISTINCT category FROM books")
    categories = [row['category'] for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM reviews ORDER BY id DESC")
    recent_reviews = cursor.fetchall()

    conn.close()

    return render_template(
        'home.html',
        books=books,
        categories=categories,
        recent_reviews=recent_reviews,
        q=q,
        category=category,
        sort=sort,
        page=page,
        total_pages=total_pages
    )

# Customer Registration Route (Auto-login & redirect to Home)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not name or not email or not password:
            flash("Please fill in all fields.", "danger")
            return redirect(url_for('register'))

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)", (name, email, password))
            conn.commit()
            
            # Automatically log in the user upon registration
            user_id = cursor.lastrowid
            session['user_id'] = user_id
            session['user_name'] = name
            session['user_email'] = email
            
            conn.close()
            flash(f"Login successful! Welcome, {name}.", "success")
            return redirect(url_for('home'))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Email address is already registered.", "danger")
            return redirect(url_for('register'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Register - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px;">
            <h4 class="fw-bold text-center mb-3">Create Customer Account</h4>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}} p-2 small text-center">{{msg}}</div>{% endfor %}{% endif %}
            {% endwith %}
            <form method="POST">
                <div class="mb-3"><label>Full Name</label><input type="text" name="name" class="form-control" required></div>
                <div class="mb-3"><label>Email Address</label><input type="email" name="email" class="form-control" required></div>
                <div class="mb-3"><label>Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Sign Up</button>
            </form>
            <div class="text-center mt-3 small">Already have an account? <a href="/login">Login here</a></div>
        </div>
    </body>
    </html>
    """)

# Customer Login Route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
        user = cursor.fetchone()
        conn.close()

        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            flash(f"Login successful! Welcome back, {user['name']}.", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password.", "danger")
            return redirect(url_for('login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Login - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px;">
            <h4 class="fw-bold text-center mb-3">Customer Login</h4>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}} p-2 small text-center">{{msg}}</div>{% endfor %}{% endif %}
            {% endwith %}
            <form method="POST">
                <div class="mb-3"><label>Email Address</label><input type="email" name="email" class="form-control" required></div>
                <div class="mb-3"><label>Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Login</button>
            </form>
            <div class="text-center mt-3 small">Don't have an account? <a href="/register">Register here</a></div>
        </div>
    </body>
    </html>
    """)

# Cart Add Route
@app.route('/cart/add/<int:book_id>', methods=['POST'])
def add_to_cart(book_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    book = cursor.fetchone()
    conn.close()

    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))

    cart = session.get('cart', {})
    current_qty = cart.get(str(book_id), 0)

    if current_qty + 1 > book['stock']:
        flash(f"Cannot add more copies. Stock limit is {book['stock']}.", "warning")
        return redirect(url_for('home'))

    cart[str(book_id)] = current_qty + 1
    session['cart'] = cart
    flash(f"Added '{book['title']}' to your cart!", "success")
    return redirect(url_for('cart'))

# Review Submission
@app.route('/review/add', methods=['POST'])
def add_review():
    name = request.form.get('name', '').strip()
    book_title = request.form.get('book_title', '').strip()
    comment = request.form.get('comment', '').strip()
    try: rating = int(request.form.get('rating', 5))
    except ValueError: rating = 5

    if not name or len(name) < 2 or not book_title or not comment or len(comment) < 5:
        flash("Please fill in all review fields correctly.", "danger")
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reviews (name, book_title, rating, comment) VALUES (?, ?, ?, ?)", (name, book_title, rating, comment))
    conn.commit()
    conn.close()

    flash("Thank you! Your review has been submitted.", "success")
    return redirect(url_for('home'))

# Cart Display
@app.route('/cart')
def cart():
    cart_items = session.get('cart', {})
    conn = get_db_connection()
    cursor = conn.cursor()

    books_in_cart = []
    total_price = 0.0

    for book_id, quantity in cart_items.items():
        cursor.execute("SELECT * FROM books WHERE id = ?", (int(book_id),))
        book = cursor.fetchone()
        if book:
            item_total = book['price'] * quantity
            total_price += item_total
            books_in_cart.append({
                'id': book['id'],
                'title': book['title'],
                'price': book['price'],
                'quantity': quantity,
                'item_total': item_total,
                'image_url': book['image_url']
            })

    conn.close()

    items_html = ""
    for item in books_in_cart:
        items_html += f"""
        <li class="list-group-item d-flex justify-content-between align-items-center py-3">
            <div class="d-flex align-items-center">
                <img src="{item['image_url']}" style="width: 50px; height: 70px; object-fit: cover;" class="rounded me-3">
                <div>
                    <h6 class="my-0">{item['title']}</h6>
                    <small class="text-muted">₹{item['price']:.2f} x {item['quantity']}</small>
                </div>
            </div>
            <span class="fw-bold">₹{item['item_total']:.2f}</span>
        </li>
        """

    if not books_in_cart:
        items_html = "<li class='list-group-item text-center py-4 text-muted'>Your cart is currently empty.</li>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Shopping Cart - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
        <div class="container bg-white p-4 rounded shadow-sm" style="max-width: 600px;">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h3 class="m-0 fw-bold">Shopping Cart</h3>
                <a href="/" class="btn btn-outline-dark btn-sm">Continue Shopping</a>
            </div>
            <ul class="list-group mb-3">{items_html}</ul>
            <div class="d-flex justify-content-between align-items-center fw-bold fs-5 mb-4">
                <span>Total Amount:</span>
                <span class="text-primary">₹{total_price:.2f}</span>
            </div>
            <a href="/checkout" class="btn btn-warning w-100 fw-bold btn-lg {'disabled' if not books_in_cart else ''}">Proceed to Order</a>
        </div>
    </body>
    </html>
    """

# Checkout Route
@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    cart_items = session.get('cart', {})
    if not cart_items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = conn.cursor()
    total_amount = 0.0

    for book_id, quantity in cart_items.items():
        cursor.execute("SELECT price FROM books WHERE id = ?", (int(book_id),))
        book = cursor.fetchone()
        if book:
            total_amount += book['price'] * quantity

    default_name = session.get('user_name', '')
    default_email = session.get('user_email', '')

    if request.method == 'POST':
        c_name = request.form.get('customer_name', '').strip()
        c_email = request.form.get('customer_email', '').strip()
        c_phone = request.form.get('customer_phone', '').strip()
        c_address = request.form.get('customer_address', '').strip()

        for book_id, quantity in cart_items.items():
            cursor.execute("UPDATE books SET stock = stock - ? WHERE id = ?", (quantity, int(book_id)))

        user_id = session.get('user_id', None)
        cursor.execute(
            '''INSERT INTO orders (user_id, customer_name, customer_email, customer_phone, customer_address, total_amount, status) 
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, c_name, c_email, c_phone, c_address, total_amount, 'Placed')
        )
        conn.commit()
        conn.close()

        session['cart'] = {}
        flash("Order placed successfully!", "success")
        return redirect(url_for('orders'))

    conn.close()

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Checkout - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
        <div class="container bg-white p-4 rounded shadow-sm" style="max-width: 550px;">
            <h3 class="fw-bold mb-3">Shipping & Customer Details</h3>
            <p class="text-muted small mb-4">Please fill in your details to complete the order.</p>
            <form method="POST">
                <div class="mb-3">
                    <label class="form-label fw-semibold">Full Name</label>
                    <input type="text" name="customer_name" class="form-control" value="{default_name}" placeholder="Enter full name" required>
                </div>
                <div class="mb-3">
                    <label class="form-label fw-semibold">Email Address</label>
                    <input type="email" name="customer_email" class="form-control" value="{default_email}" placeholder="name@example.com" required>
                </div>
                <div class="mb-3">
                    <label class="form-label fw-semibold">Phone Number</label>
                    <input type="tel" name="customer_phone" class="form-control" placeholder="Enter mobile number" required>
                </div>
                <div class="mb-3">
                    <label class="form-label fw-semibold">Delivery Address</label>
                    <textarea name="customer_address" class="form-control" rows="3" placeholder="Enter complete home/office address..." required></textarea>
                </div>
                <div class="p-3 bg-light rounded d-flex justify-content-between align-items-center mb-4">
                    <span class="fw-bold">Total Payable Amount:</span>
                    <span class="fs-4 fw-bold text-success">₹{total_amount:.2f}</span>
                </div>
                <div class="d-flex gap-2">
                    <a href="/cart" class="btn btn-outline-secondary w-50">Back to Cart</a>
                    <button type="submit" class="btn btn-success w-50 fw-bold">Confirm & Place Order</button>
                </div>
            </form>
        </div>
    </body>
    </html>
    """

# Wishlist Route & Alias Endpoints
@app.route('/wishlist', endpoint='wishlist')
@app.route('/view_wishlist', endpoint='view_wishlist')
def wishlist():
    wishlist_ids = session.get('wishlist', [])
    conn = get_db_connection()
    cursor = conn.cursor()

    wishlist_books = []
    if wishlist_ids:
        placeholders = ','.join(['?'] * len(wishlist_ids))
        cursor.execute(f"SELECT * FROM books WHERE id IN ({placeholders})", wishlist_ids)
        wishlist_books = cursor.fetchall()

    conn.close()

    books_html = ""
    for book in wishlist_books:
        books_html += f"""
        <div class="col-md-3 mb-3">
            <div class="card h-100 p-2 shadow-sm">
                <img src="{book['image_url']}" class="card-img-top" style="height:200px; object-fit:cover;">
                <div class="card-body p-2">
                    <h6 class="fw-bold">{book['title']}</h6>
                    <p class="text-primary fw-bold mb-2">₹{book['price']:.2f}</p>
                    <a href="/" class="btn btn-outline-dark btn-sm w-100">View in Store</a>
                </div>
            </div>
        </div>
        """

    if not wishlist_books:
        books_html = "<div class='col-12 text-center py-5 text-muted'>Your wishlist is empty. Tap the heart on any book to add it!</div>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>My Wishlist - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
        <div class="container bg-white p-4 rounded shadow-sm">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h3 class="m-0 fw-bold">❤️ My Saved Wishlist</h3>
                <a href="/" class="btn btn-outline-dark btn-sm">Back to Bookstore</a>
            </div>
            <div class="row">{books_html}</div>
        </div>
    </body>
    </html>
    """

@app.route('/wishlist/toggle/<int:book_id>', methods=['POST'])
def toggle_wishlist(book_id):
    wishlist = session.get('wishlist', [])
    if book_id in wishlist:
        wishlist.remove(book_id)
        added = False
    else:
        wishlist.append(book_id)
        added = True
    session['wishlist'] = wishlist
    return jsonify({'added': added})

# User Orders Route
@app.route('/orders')
def orders():
    user_id = session.get('user_id')
    user_email = session.get('user_email')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if user_id:
        cursor.execute("SELECT * FROM orders WHERE user_id = ? OR customer_email = ? ORDER BY id DESC", (user_id, user_email))
    else:
        cursor.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 5")
        
    user_orders = cursor.fetchall()
    conn.close()

    orders_html = ""
    for order in user_orders:
        orders_html += f"""
        <div class="card mb-3 p-3 shadow-sm border-0">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <h6 class="fw-bold mb-1">Order #{order['id']}</h6>
                    <small class="text-muted d-block">Date: {order['created_at']}</small>
                    <small class="text-secondary d-block mt-1">
                        <strong>Deliver To:</strong> {order['customer_name'] or 'N/A'}<br>
                        <strong>Address:</strong> {order['customer_address'] or 'N/A'}
                    </small>
                </div>
                <div class="text-end">
                    <span class="badge bg-success mb-1">{order['status']}</span>
                    <div class="fw-bold text-primary">₹{order['total_amount']:.2f}</div>
                </div>
            </div>
        </div>
        """

    if not user_orders:
        orders_html = "<div class='text-center py-5 text-muted'>No past orders found.</div>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>My Orders - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
        <div class="container bg-white p-4 rounded shadow-sm" style="max-width: 600px;">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h3 class="m-0 fw-bold">📦 Customer Orders</h3>
                <a href="/" class="btn btn-outline-dark btn-sm">Home</a>
            </div>
            {orders_html}
        </div>
    </body>
    </html>
    """

# Admin Login Route
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if username == 'admin' and password == 'admin123':
            session['admin_logged_in'] = True
            flash("Logged in as Administrator.", "success")
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Invalid admin credentials. Use admin / admin123.", "danger")
            return redirect(url_for('admin_login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Admin Login - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-dark d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-lg" style="width: 360px;">
            <h4 class="fw-bold text-center mb-3">Admin Portal</h4>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }} p-2 small text-center">{{ message }}</div>{% endfor %}{% endif %}
            {% endwith %}
            <form method="POST">
                <div class="mb-3"><label class="form-label">Username</label><input type="text" name="username" class="form-control" placeholder="admin" required></div>
                <div class="mb-3"><label class="form-label">Password</label><input type="password" name="password" class="form-control" placeholder="admin123" required></div>
                <button type="submit" class="btn btn-warning w-100 fw-bold">Login</button>
            </form>
            <a href="/" class="btn btn-link btn-sm text-center mt-3 text-decoration-none">Back to Storefront</a>
        </div>
    </body>
    </html>
    """)

# Admin Dashboard
@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        flash("Unauthorized access. Please login first.", "danger")
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    orders_list = cursor.fetchall()
    conn.close()

    return render_template('admin.html', orders=orders_list)

# Export Orders CSV
@app.route('/admin/export_orders', endpoint='export_orders')
def export_orders():
    if not session.get('admin_logged_in'):
        flash("Unauthorized access.", "danger")
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders")
    orders_list = cursor.fetchall()
    conn.close()

    csv_data = "Order ID,Customer Name,Email,Phone,Address,Total Amount,Discount,Status,Created At\n"
    for o in orders_list:
        csv_data += f"{o['id']},{o['customer_name'] or 'Guest'},{o['customer_email'] or ''},{o['customer_phone'] or ''},\"{o['customer_address'] or ''}\",{o['total_amount']:.2f},{o['discount_amount'] or 0.0},{o['status']},{o['created_at']}\n"

    return csv_data, 200, {
        'Content-Type': 'text/csv',
        'Content-Disposition': 'attachment; filename=orders_report.csv'
    }

# Update Order Status
@app.route('/admin/order/update_status/<int:order_id>', methods=['POST'], endpoint='update_order_status')
def update_order_status(order_id):
    if not session.get('admin_logged_in'):
        flash("Unauthorized access.", "danger")
        return redirect(url_for('admin_login'))

    new_status = request.form.get('status', 'Placed')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    conn.commit()
    conn.close()

    flash(f"Order #{order_id} status updated to '{new_status}'.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash("Admin logged out successfully.", "info")
    return redirect(url_for('admin_login'))

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('home'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)