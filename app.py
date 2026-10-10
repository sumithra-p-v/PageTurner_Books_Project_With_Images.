import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, flash

app = Flask(__name__)
app.secret_key = 'super_secret_pageturner_key'
DATABASE = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Hand-rolled CSRF Protection Hook & Decorator
@app.before_request
def ensure_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)

def csrf_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'POST':
            token_in_form = request.form.get('csrf_token')
            token_in_session = session.get('csrf_token')
            if not token_in_form or not token_in_session or token_in_form != token_in_session:
                return "400 Bad Request: Invalid or missing CSRF token.", 400
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('user_id') and not session.get('admin_logged_in'):
                flash("Please log in to access this page.", "warning")
                return redirect(url_for('admin_login'))
            
            user_role = session.get('user_role', 'customer')
            if user_role not in allowed_roles:
                return """
                <!DOCTYPE html>
                <html>
                <head><title>403 Forbidden - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
                <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
                    <div class="card p-5 text-center shadow-sm" style="max-width: 450px;">
                        <h1 class="display-1 text-danger fw-bold">403</h1>
                        <h4 class="fw-bold mb-3">Access Forbidden</h4>
                        <p class="text-muted">You do not have permission to access this resource.</p>
                        <a href="/" class="btn btn-primary mt-3 fw-bold">Return to Storefront</a>
                    </div>
                </body>
                </html>
                """, 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            name TEXT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    for col, col_type in [("username", "TEXT"), ("name", "TEXT"), ("role", "TEXT DEFAULT 'customer'")]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
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
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_type TEXT NOT NULL, 
            discount_value REAL NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            change INTEGER NOT NULL,
            reason TEXT NOT NULL,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wishlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (book_id) REFERENCES books (id)
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

    for col_name, col_type in [("customer_name", "TEXT"), ("customer_email", "TEXT"), ("customer_phone", "TEXT"), ("customer_address", "TEXT"), ("discount_amount", "REAL DEFAULT 0.0")]:
        try:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            line1 TEXT NOT NULL,
            city TEXT NOT NULL,
            pincode TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price_at_purchase REAL NOT NULL DEFAULT 0.0,
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (book_id) REFERENCES books (id)
        )
    ''')

    try:
        cursor.execute("ALTER TABLE order_items ADD COLUMN price_at_purchase REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip_or_email TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            locked_until TIMESTAMP
        )
    ''')

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
        cursor.executemany('INSERT INTO books (title, author, price, rating, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)', sample_books)

    cursor.execute('SELECT COUNT(*) FROM coupons')
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES ('SAVE10', 'percentage', 10.0, 1)")
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES ('FLAT100', 'fixed', 100.0, 1)")

    conn.commit()
    conn.close()

def get_recommendations(book_id, limit=4):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT b.*, COUNT(o2.book_id) AS frequency
        FROM order_items o1
        JOIN order_items o2 ON o1.order_id = o2.order_id AND o1.book_id != o2.book_id
        JOIN books b ON o2.book_id = b.id
        WHERE o1.book_id = ?
        GROUP BY o2.book_id
        ORDER BY frequency DESC
        LIMIT ?
    """
    cursor.execute(query, (book_id, limit))
    recs = cursor.fetchall()
    conn.close()
    return recs

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

    return render_template('home.html', books=books, categories=categories, recent_reviews=recent_reviews, q=q, category=category, sort=sort, page=page, total_pages=total_pages)

@app.route('/review/add', methods=['POST'])
@csrf_required
def add_review():
    name = request.form.get('name', '').strip()
    book_title = request.form.get('book_title', '').strip()
    rating = request.form.get('rating', '5')
    comment = request.form.get('comment', '').strip()

    if name and book_title and comment:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reviews (name, book_title, rating, comment) VALUES (?, ?, ?, ?)", (name, book_title, rating, comment))
        conn.commit()
        conn.close()
        flash("Thank you! Your review has been submitted.", "success")
    else:
        flash("Please fill out all fields to submit a review.", "danger")
    return redirect(url_for('home'))

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    book = cursor.fetchone()
    conn.close()

    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))

    recs = get_recommendations(book_id)
    recs_cards = ""
    for r in recs:
        recs_cards += f"""
        <div class="col-md-3">
            <div class="card h-100 shadow-sm border-0 text-center p-2">
                <img src="{r['image_url']}" class="card-img-top mx-auto" style="height: 140px; object-fit: contain;">
                <div class="card-body p-2">
                    <h6 class="fw-bold mb-1 text-truncate">{r['title']}</h6>
                    <small class="text-muted d-block">{r['author']}</small>
                    <span class="fw-bold text-primary">₹{r['price']:.2f}</span>
                    <a href="/book/{r['id']}" class="btn btn-sm btn-outline-primary w-100 mt-2">View</a>
                </div>
            </div>
        </div>
        """

    recs_section = f"""
    <div class="mt-5">
        <h5 class="fw-bold mb-3">🛒 Customers Also Bought</h5>
        <div class="row g-3">
            {recs_cards}
        </div>
    </div>
    """ if recs else ""

    csrf_tok = session.get('csrf_token', '')

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>""" + book['title'] + """ - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
    <div class="container bg-white p-4 rounded shadow-sm" style="max-width: 800px;">
        <div class="row align-items-center">
            <div class="col-md-4 text-center">
                <img src=" """ + book['image_url'] + """ " class="img-fluid rounded" style="max-height: 250px;">
            </div>
            <div class="col-md-8">
                <h3 class="fw-bold">""" + book['title'] + """</h3>
                <h6 class="text-muted">by """ + book['author'] + """</h6>
                <p class="mt-2"><span class="badge bg-secondary">""" + book['category'] + """</span> <span class="badge bg-warning text-dark">""" + str(book['rating']) + """ ⭐</span></p>
                <h4 class="text-primary fw-bold">₹""" + f"{book['price']:.2f}" + """</h4>
                <p class="text-muted small">Stock Available: """ + str(book['stock']) + """</p>
                <form method="POST" action="/cart/add/""" + str(book['id']) + """">
                    <input type="hidden" name="csrf_token" value=" """ + csrf_tok + """ ">
                    <button type="submit" class="btn btn-warning fw-bold">Add to Cart</button>
                </form>
                <form method="POST" action="/wishlist/add/""" + str(book['id']) + """" class="mt-2">
                    <input type="hidden" name="csrf_token" value=" """ + csrf_tok + """ ">
                    <button type="submit" class="btn btn-outline-danger btn-sm fw-bold">❤️ Add to Wishlist</button>
                </form>
            </div>
        </div>
        """ + recs_section + """
        <hr class="my-4">
        <a href="/" class="btn btn-outline-dark">Return to Catalog</a>
    </div>
    </body>
    </html>
    """)

@app.route('/register', methods=['GET', 'POST'])
@csrf_required
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
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            conn.close()
            flash("This email is already registered. Please log in instead.", "danger")
            return redirect(url_for('login'))

        cursor.execute("INSERT INTO users (username, name, email, password, role) VALUES (?, ?, ?, ?, 'customer')", (email, name, email, password))
        conn.commit()
        session['user_id'] = cursor.lastrowid
        session['user_name'] = name
        session['user_email'] = email
        session['user_role'] = 'customer'
        conn.close()
        flash(f"Registration successful! Welcome, {name}.", "success")
        return redirect(url_for('home'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Register - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px;">
            <h4 class="fw-bold text-center mb-3">Create Customer Account</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
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

@app.route('/login', methods=['GET', 'POST'])
@csrf_required
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        identifier = request.remote_addr

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT attempts, locked_until FROM login_attempts WHERE ip_or_email = ?", (identifier,))
        record = cursor.fetchone()

        if record and record['locked_until']:
            lock_time = datetime.strptime(record['locked_until'], "%Y-%m-%d %H:%M:%S.%f") if "." in record['locked_until'] else datetime.strptime(record['locked_until'], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < lock_time:
                conn.close()
                flash("Account temporarily locked. Try again in 5 minutes.", "danger")
                return redirect(url_for('login'))

        cursor.execute("SELECT * FROM users WHERE (email = ? OR username = ?) AND password = ?", (email, email, password))
        user = cursor.fetchone()

        if user:
            cursor.execute("DELETE FROM login_attempts WHERE ip_or_email = ?", (identifier,))
            conn.commit()
            conn.close()
            session['user_id'] = user['id']
            session['user_name'] = user['name'] or user['username']
            session['user_email'] = user['email']
            session['user_role'] = user['role'] if 'role' in user.keys() and user['role'] else 'customer'
            flash(f"Welcome back, {session['user_name']}.", "success")
            return redirect(url_for('home'))
        else:
            current_attempts = (record['attempts'] + 1) if record else 1
            lock_until = datetime.now() + timedelta(minutes=5) if current_attempts >= 5 else (datetime.now() if not record else record['locked_until'])
            cursor.execute("INSERT OR REPLACE INTO login_attempts (ip_or_email, attempts, locked_until) VALUES (?, ?, ?)", (identifier, current_attempts, lock_until))
            conn.commit()
            conn.close()
            if current_attempts >= 5:
                flash("Too many failed attempts. Account locked for 5 minutes.", "danger")
            else:
                flash(f"Invalid credentials. Attempt {current_attempts} of 5.", "danger")
            return redirect(url_for('login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Login - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px;">
            <h4 class="fw-bold text-center mb-3">Customer Login</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="mb-3"><label>Email Address</label><input type="email" name="email" class="form-control" required></div>
                <div class="mb-3"><label>Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Login</button>
            </form>
            <div class="d-flex justify-content-between mt-3 small">
                <a href="/register">Register here</a>
                <a href="/forgot_password" class="text-muted">Forgot password?</a>
            </div>
        </div>
    </body>
    </html>
    """)

@app.route('/forgot_password', methods=['GET', 'POST'])
@csrf_required
def forgot_password():
    reset_link = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(minutes=15)
            cursor.execute("INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)", (user['id'], token, expires_at))
            conn.commit()
            reset_link = url_for('reset_password', token=token, _external=True)
            flash("Password reset link generated.", "info")
        else:
            flash("No account found with that email address.", "danger")
        conn.close()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Forgot Password - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 400px;">
            <h4 class="fw-bold text-center mb-3">🔑 Forgot Password</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="mb-3"><label class="form-label">Email Address</label><input type="email" name="email" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Generate Reset Link</button>
            </form>
            {% if reset_link %}<div class="mt-3 p-2 bg-light border rounded small"><a href="{{ reset_link }}">{{ reset_link }}</a></div>{% endif %}
            <div class="text-center mt-3 small"><a href="/login">Back to Login</a></div>
        </div>
    </body>
    </html>
    """, reset_link=reset_link)

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
@csrf_required
def reset_password(token):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reset_tokens WHERE token = ?", (token,))
    token_record = cursor.fetchone()
    if not token_record or token_record['used'] == 1:
        conn.close()
        flash("Invalid or used reset token.", "danger")
        return redirect(url_for('forgot_password'))
    conn.close()

    if request.method == 'POST':
        new_password = request.form.get('password', '').strip()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, token_record['user_id']))
        cursor.execute("UPDATE reset_tokens SET used = 1 WHERE id = ?", (token_record['id'],))
        conn.commit()
        conn.close()
        flash("Password successfully updated! Please log in.", "success")
        return redirect(url_for('login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Reset Password - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px;">
            <h4 class="fw-bold text-center mb-3">Set New Password</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="mb-3"><label class="form-label">New Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-success w-100 fw-bold">Update Password</button>
            </form>
        </div>
    </body>
    </html>
    """)

@app.route('/cart/add/<int:book_id>', methods=['GET', 'POST'])
@csrf_required
def add_to_cart(book_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    book = cursor.fetchone()
    conn.close()
    if not book:
        return redirect(url_for('home'))
    cart = session.get('cart', {})
    cart[str(book_id)] = cart.get(str(book_id), 0) + 1
    session['cart'] = cart
    flash(f"Added '{book['title']}' to cart!", "success")
    return redirect(url_for('cart'))

@app.route('/coupon/apply', methods=['POST'])
@csrf_required
def apply_coupon():
    code = request.form.get('coupon_code', '').strip().upper()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coupons WHERE code = ? AND is_active = 1", (code,))
    coupon = cursor.fetchone()
    conn.close()

    if coupon:
        session['applied_coupon'] = {
            'code': coupon['code'],
            'discount_type': coupon['discount_type'],
            'discount_value': coupon['discount_value']
        }
        flash(f"Promo code '{code}' applied successfully!", "success")
    else:
        flash("Invalid or expired coupon code.", "danger")
    return redirect(url_for('cart'))

@app.route('/coupon/remove')
def remove_coupon():
    session.pop('applied_coupon', None)
    flash("Promo code removed.", "info")
    return redirect(url_for('cart'))

@app.route('/wishlist/add/<int:book_id>', methods=['POST'])
@csrf_required
def add_to_wishlist(book_id):
    if not session.get('user_id'):
        flash("Please log in to manage your wishlist.", "warning")
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM wishlist WHERE user_id = ? AND book_id = ?", (user_id, book_id))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO wishlist (user_id, book_id) VALUES (?, ?)", (user_id, book_id))
        conn.commit()
        flash("Added book to your wishlist!", "success")
    else:
        flash("Book is already in your wishlist.", "info")
    conn.close()
    return redirect(url_for('wishlist'))

@app.route('/wishlist')
def wishlist():
    if not session.get('user_id'):
        flash("Please log in to view your wishlist.", "warning")
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT b.*, w.id as wishlist_id 
        FROM wishlist w 
        JOIN books b ON w.book_id = b.id 
        WHERE w.user_id = ?
    """, (user_id,))
    wishlist_items = cursor.fetchall()
    conn.close()

    items_html = ""
    for item in wishlist_items:
        items_html += f"""
        <div class="card mb-3 p-3 d-flex flex-row justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-3">
                <img src="{item['image_url']}" style="height: 60px; object-fit: contain;">
                <div>
                    <h6 class="fw-bold mb-1">{item['title']}</h6>
                    <small class="text-muted">by {item['author']}</small>
                    <div class="text-primary fw-bold mt-1">₹{item['price']:.2f}</div>
                </div>
            </div>
            <div class="d-flex gap-2">
                <form method="POST" action="/cart/add/{item['id']}">
                    <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
                    <button type="submit" class="btn btn-sm btn-warning fw-bold">Move to Cart</button>
                </form>
                <a href="/wishlist/remove/{item['wishlist_id']}" class="btn btn-sm btn-outline-danger">Remove</a>
            </div>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>My Wishlist - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
    <div class="container bg-white p-4 rounded shadow-sm" style="max-width: 700px;">
        <h3 class="fw-bold mb-3">❤️ My Wishlist</h3>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}} p-2 small">{{msg}}</div>{% endfor %}{% endif %}
        {% endwith %}
        """ + (items_html or "<p class='text-muted text-center py-4'>Your wishlist is empty.</p>") + """
        <a href="/" class="btn btn-dark w-100 mt-3">Back to Storefront</a>
    </div>
    </body>
    </html>
    """)

@app.route('/wishlist/remove/<int:wishlist_id>')
def remove_from_wishlist(wishlist_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM wishlist WHERE id = ?", (wishlist_id,))
    conn.commit()
    conn.close()
    flash("Item removed from wishlist.", "info")
    return redirect(url_for('wishlist'))

@app.route('/cart')
def cart():
    cart_items = session.get('cart', {})
    conn = get_db_connection()
    cursor = conn.cursor()
    books_in_cart, subtotal = [], 0.0
    for book_id, qty in cart_items.items():
        cursor.execute("SELECT * FROM books WHERE id = ?", (int(book_id),))
        book = cursor.fetchone()
        if book:
            item_total = book['price'] * qty
            subtotal += item_total
            books_in_cart.append({'title': book['title'], 'price': book['price'], 'quantity': qty, 'item_total': item_total, 'image_url': book['image_url']})
    conn.close()

    discount = 0.0
    applied_coupon = session.get('applied_coupon')
    if applied_coupon and books_in_cart:
        if applied_coupon['discount_type'] == 'percentage':
            discount = subtotal * (applied_coupon['discount_value'] / 100.0)
        elif applied_coupon['discount_type'] == 'fixed':
            discount = min(subtotal, applied_coupon['discount_value'])

    final_total = max(0.0, subtotal - discount)
    session['calculated_total'] = final_total
    session['applied_discount'] = discount

    items_html = "".join([f"<li class='list-group-item d-flex justify-content-between align-items-center'><span>{i['title']} (x{i['quantity']})</span><span>₹{i['item_total']:.2f}</span></li>" for i in books_in_cart]) or "<li class='list-group-item text-center text-muted'>Cart is empty</li>"
    
    coupon_section = f"""
    <form method="POST" action="/coupon/apply" class="input-group mb-3">
        <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
        <input type="text" name="coupon_code" class="form-control" placeholder="Promo Code (e.g. SAVE10)" required>
        <button class="btn btn-outline-secondary" type="submit">Apply Coupon</button>
    </form>
    """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Cart - PageTurner</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
    <div class="container bg-white p-4 rounded shadow-sm" style="max-width: 600px;">
        <h3 class="fw-bold mb-3">Shopping Cart</h3>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}} p-2 small">{{msg}}</div>{% endfor %}{% endif %}
        {% endwith %}
        <ul class="list-group mb-3">""" + items_html + """</ul>
        """ + coupon_section + f"""
        <div class="d-flex justify-content-between text-muted small mb-1"><span>Subtotal:</span><span>₹{subtotal:.2f}</span></div>
        <div class="d-flex justify-content-between text-muted small mb-2"><span>Discount:</span><span>-₹{discount:.2f}</span></div>
        <div class="d-flex justify-content-between fw-bold fs-5 mb-3"><span>Total Payable:</span><span class="text-primary">₹{final_total:.2f}</span></div>
        <a href="/checkout" class="btn btn-warning w-100 fw-bold {'disabled' if not books_in_cart else ''}">Proceed to Checkout</a>
        <a href="/" class="btn btn-outline-dark w-100 mt-2">Back to Home Storefront</a>
    </div>
    </body>
    </html>
    """)

@app.route('/checkout', methods=['GET', 'POST'])
@csrf_required
def checkout():
    cart = session.get('cart', {})
    if not cart: return redirect(url_for('home'))
    total = session.get('calculated_total', 0.0)
    discount = session.get('applied_discount', 0.0)
    
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("""
                INSERT INTO orders (user_id, customer_name, customer_email, customer_phone, customer_address, total_amount, discount_amount, status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Placed')
            """, (session.get('user_id'), request.form.get('name'), request.form.get('email'), request.form.get('phone'), request.form.get('address'), total, discount))
            order_id = cursor.lastrowid

            for book_id_str, qty in cart.items():
                book_id = int(book_id_str)
                cursor.execute("SELECT price, stock FROM books WHERE id = ?", (book_id,))
                book = cursor.fetchone()
                if not book or book['stock'] < qty:
                    raise Exception(f"Insufficient stock for book ID {book_id}")
                
                cursor.execute("UPDATE books SET stock = stock - ? WHERE id = ?", (qty, book_id))
                cursor.execute("INSERT INTO order_items (order_id, book_id, quantity, price_at_purchase) VALUES (?, ?, ?, ?)", (order_id, book_id, qty, book['price']))
                cursor.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, 'purchase', ?)", (book_id, -qty, session.get('user_id')))

            conn.commit()
            session['cart'] = {}
            session.pop('applied_coupon', None)
            flash("Order placed successfully!", "success")
            return redirect(url_for('orders'))
        except Exception as e:
            conn.rollback()
            flash(f"Checkout failed: {str(e)}", "danger")
            return redirect(url_for('cart'))
        finally:
            conn.close()

    name = session.get('user_name', '')
    email = session.get('user_email', '')
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Checkout</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4"><div class="container bg-white p-4 rounded" style="max-width: 600px;"><h3>Checkout</h3>
    <form method="POST">
    <input type="hidden" name="csrf_token" value=""" + f'"{session.get("csrf_token", "")}"' + """>
    <input type="text" name="name" value=""" + f'"{name}"' + """ class="form-control mb-2" placeholder="Full Name" required>
    <input type="email" name="email" value=""" + f'"{email}"' + """ class="form-control mb-2" placeholder="Email" required>
    <input type="tel" name="phone" class="form-control mb-2" placeholder="Phone" required>
    <textarea name="address" class="form-control mb-2" placeholder="Delivery Address" required></textarea>
    <h5 class="my-3 text-success">Total Payable: ₹""" + f"{total:.2f}" + f" (Saved ₹{discount:.2f})" + """</h5><button type="submit" class="btn btn-success w-100">Place Order</button></form></div></body></html>
    """)

@app.route('/orders')
def orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    user_orders = cursor.fetchall()
    conn.close()

    orders_html = ""
    for o in user_orders:
        cancel_btn = f"""
        <form method="POST" action="/order/cancel/{o['id']}" class="mt-2">
            <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
            <button type="submit" class="btn btn-sm btn-outline-danger w-100 fw-bold">Cancel & Refund Order</button>
        </form>
        """ if o['status'] == 'Placed' else ""

        orders_html += f"""
        <div class='card mb-3 p-3'>
            <h5>Order #{o['id']}</h5>
            <p class='mb-1'><b>Status:</b> <span class="badge bg-secondary">{o['status']}</span></p>
            <p class='mb-0'><b>Total:</b> ₹{o['total_amount']:.2f} (Discount applied: ₹{o['discount_amount'] or 0.0:.2f})</p>
            {cancel_btn}
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html><html><head><title>Orders</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4"><div class="container bg-white p-4 rounded" style="max-width: 600px;"><h3>My Orders</h3>""" + (orders_html or "<p>No orders.</p>") + """<a href="/" class="btn btn-dark w-100 mt-3">Back to Home</a></div></body></html>
    """)

@app.route('/order/cancel/<int:order_id>', methods=['POST'])
@csrf_required
def cancel_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        if not order:
            raise Exception("Order not found.")
        if order['status'] != 'Placed':
            raise Exception("Only 'Placed' orders can be cancelled.")
            
        cursor.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
        items = cursor.fetchall()
        for item in items:
            cursor.execute("UPDATE books SET stock = stock + ? WHERE id = ?", (item['quantity'], item['book_id']))
            cursor.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, 'cancelled_order', ?)", (item['book_id'], item['quantity'], session.get('user_id')))
            
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = ?", (order_id,))
        conn.commit()
        flash(f"Order #{order_id} cancelled and refunded.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()
    return redirect(url_for('orders'))

@app.route('/admin/login', methods=['GET', 'POST'])
@csrf_required
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if username == 'admin' and password == 'admin123':
            session['admin_logged_in'] = True
            session['user_role'] = 'admin'
            return redirect(url_for('admin_dashboard'))
        elif username == 'staff' and password == 'staff123':
            session['admin_logged_in'] = True
            session['user_role'] = 'staff'
            return redirect(url_for('admin_dashboard'))
            
        flash("Invalid credentials.", "danger")
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Login</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-dark d-flex align-items-center justify-content-center min-vh-100"><div class="card p-4 shadow" style="width: 350px;">
    <h4 class="text-center mb-3 text-white">Admin / Staff Login</h4><form method="POST">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <input type="text" name="username" class="form-control mb-2" placeholder="admin or staff" required>
    <input type="password" name="password" class="form-control mb-3" placeholder="password" required><button type="submit" class="btn btn-warning w-100 fw-bold">Login</button></form></div></body></html>
    """)

@app.route('/admin/dashboard')
@role_required('admin', 'staff')
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    orders_list = cursor.fetchall()
    conn.close()
    return render_template('admin.html', orders=orders_list)

@app.route('/admin/coupons', methods=['GET', 'POST'])
@role_required('admin', 'staff')
@csrf_required
def admin_coupons():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        dtype = request.form.get('discount_type', 'percentage')
        dval = float(request.form.get('discount_value', 0.0))
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES (?, ?, ?, 1)", (code, dtype, dval))
        conn.commit()
        flash("Coupon created successfully!", "success")
    
    cursor.execute("SELECT * FROM coupons ORDER BY id DESC")
    coupons = cursor.fetchall()
    conn.close()

    rows = "".join([f"<tr><td>{c['id']}</td><td class='fw-bold'>{c['code']}</td><td>{c['discount_type']}</td><td>{c['discount_value']}</td><td><a href='/admin/coupon/delete/{c['id']}' class='btn btn-sm btn-outline-danger'>Delete</a></td></tr>" for c in coupons])
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Manage Coupons</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4"><div class="container bg-white p-4 rounded shadow-sm">
    <div class="d-flex justify-content-between align-items-center mb-4"><h3 class="m-0">🏷️ Promo Codes Management</h3><a href="/admin/dashboard" class="btn btn-outline-dark btn-sm">Dashboard</a></div>
    <form method="POST" class="row g-2 mb-4">
    <input type="hidden" name="csrf_token" value=""" + f'"{session.get("csrf_token", "")}"' + """>
    <div class="col-md-4"><input type="text" name="code" class="form-control" placeholder="Code (e.g., SUMMER20)" required></div>
    <div class="col-md-3"><select name="discount_type" class="form-select"><option value="percentage">Percentage (%)</option><option value="fixed">Fixed (₹)</option></select></div>
    <div class="col-md-3"><input type="number" step="0.01" name="discount_value" class="form-control" placeholder="Value" required></div>
    <div class="col-md-2"><button type="submit" class="btn btn-success w-100">Add Code</button></div>
    </form>
    <table class="table"><thead><tr><th>ID</th><th>Code</th><th>Type</th><th>Value</th><th>Action</th></tr></thead><tbody>""" + rows + """</tbody></table>
    </div></body></html>
    """)

@app.route('/admin/coupon/delete/<int:coupon_id>')
@role_required('admin', 'staff')
def delete_coupon(coupon_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    conn.commit()
    conn.close()
    flash("Coupon removed.", "info")
    return redirect(url_for('admin_coupons'))

@app.route('/admin/export_orders', endpoint='export_orders')
@role_required('admin', 'staff')
def export_orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders")
    orders_list = cursor.fetchall()
    conn.close()
    csv_data = "Order ID,Customer Name,Email,Phone,Address,Total Amount,Discount,Status,Created At\n"
    for o in orders_list:
        csv_data += f"{o['id']},{o['customer_name'] or 'Guest'},{o['customer_email'] or ''},{o['customer_phone'] or ''},\"{o['customer_address'] or ''}\",{o['total_amount']:.2f},{o['discount_amount'] or 0.0},{o['status']},{o['created_at']}\n"
    return csv_data, 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=orders_report.csv'}

@app.route('/admin/analytics')
@role_required('admin', 'staff')
def admin_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(total_amount), AVG(total_amount) FROM orders")
    row = cursor.fetchone()
    total_orders, total_revenue, avg_order_value = row[0] or 0, row[1] or 0.0, row[2] or 0.0
    conn.close()
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Analytics</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4"><div class="container bg-white p-4 rounded"><h3>Sales Analytics</h3>
    <div class="row text-center my-4"><div class="col-md-4"><h4>Revenue: ₹""" + f"{total_revenue:.2f}" + """</h4></div>
    <div class="col-md-4"><h4>Orders: """ + str(total_orders) + """</h4></div><div class="col-md-4"><h4>AOV: ₹""" + f"{avg_order_value:.2f}" + """</h4></div></div>
    <a href="/admin/dashboard" class="btn btn-dark">Back to Dashboard</a></div></body></html>
    """)

@app.route('/admin/inventory', methods=['GET', 'POST'])
@role_required('admin', 'staff')
@csrf_required
def admin_inventory():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title')
        author = request.form.get('author')
        price = float(request.form.get('price', 0))
        stock = int(request.form.get('stock', 10))
        category = request.form.get('category')
        image_url = request.form.get('image_url', 'https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400')
        
        cursor.execute("INSERT INTO books (title, author, price, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                       (title, author, price, stock, category, image_url))
        book_id = cursor.lastrowid
        cursor.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, 'admin_edit', ?)", (book_id, stock, session.get('user_id')))
        conn.commit()
        flash("Book added successfully!", "success")

    cursor.execute("SELECT * FROM books ORDER BY id DESC")
    books = cursor.fetchall()
    conn.close()
    
    books_rows = "".join([f"<tr><td>{b['id']}</td><td class='fw-bold'>{b['title']}</td><td>{b['author']}</td><td>₹{b['price']:.2f}</td><td>{b['stock']}</td><td><span class='badge bg-secondary'>{b['category']}</span></td></tr>" for b in books])
    
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Inventory Control</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light p-4">
    <div class="container bg-white p-4 rounded shadow-sm">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h3 class="fw-bold m-0">📦 Inventory Control & Add Book</h3>
            <a href="/admin/dashboard" class="btn btn-outline-dark btn-sm">Dashboard</a>
        </div>
        
        <!-- Add Book Form -->
        <div class="card p-3 mb-4 bg-light border-0">
            <h5 class="fw-bold mb-3">Add New Book</h5>
            <form method="POST" class="row g-2">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="col-md-3"><input type="text" name="title" class="form-control form-control-sm" placeholder="Book Title" required></div>
                <div class="col-md-2"><input type="text" name="author" class="form-control form-control-sm" placeholder="Author" required></div>
                <div class="col-md-2"><input type="number" step="0.01" name="price" class="form-control form-control-sm" placeholder="Price (₹)" required></div>
                <div class="col-md-1"><input type="number" name="stock" class="form-control form-control-sm" placeholder="Stock" required></div>
                <div class="col-md-2"><input type="text" name="category" class="form-control form-control-sm" placeholder="Category" required></div>
                <div class="col-md-2"><button type="submit" class="btn btn-success btn-sm w-100 fw-bold">Add Book</button></div>
            </form>
        </div>

        <table class="table align-middle">
            <thead class="table-dark">
                <tr><th>ID</th><th>Title</th><th>Author</th><th>Price</th><th>Stock</th><th>Category</th></tr>
            </thead>
            <tbody>""" + books_rows + """</tbody>
        </table>
    </div>
    </body>
    </html>
    """)

@app.route('/admin/reviews')
@role_required('admin', 'staff')
def admin_reviews():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reviews ORDER BY id DESC")
    reviews = cursor.fetchall()
    conn.close()

    rows = "".join([f"<tr><td>{r['id']}</td><td class='fw-bold'>{r['name']}</td><td>{r['book_title']}</td><td><span class='badge bg-warning text-dark'>{r['rating']} ⭐</span></td><td>{r['comment']}</td><td><a href='/admin/review/delete/{r['id']}' class='btn btn-sm btn-outline-danger'>Delete</a></td></tr>" for r in reviews])
    
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Review Moderation - PageTurner Admin</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body class="bg-light p-4">
        <div class="container bg-white p-4 rounded shadow-sm">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h3 class="fw-bold m-0">💬 Customer Review Moderation</h3>
                <a href="/admin/dashboard" class="btn btn-outline-dark btn-sm">Return to Dashboard</a>
            </div>
            <table class="table table-hover align-middle">
                <thead class="table-dark">
                    <tr>
                        <th>ID</th>
                        <th>Reviewer</th>
                        <th>Book Title</th>
                        <th>Rating</th>
                        <th>Comment</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    """ + (rows or '<tr><td colspan="6" class="text-center text-muted py-4">No reviews submitted yet.</td></tr>') + """
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """)

@app.route('/admin/review/delete/<int:review_id>')
@role_required('admin', 'staff')
def delete_review(review_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    conn.commit()
    conn.close()
    flash("Review deleted successfully.", "info")
    return redirect(url_for('admin_reviews'))

@app.route('/admin/order/update_status/<int:order_id>', methods=['POST'])
@role_required('admin', 'staff')
@csrf_required
def update_order_status(order_id: int):
    new_status = request.form.get('status', 'Placed')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    conn.commit()
    conn.close()
    flash(f"Order #{order_id} status updated.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)