# PageTurner Books — Updated Project

An online bookstore built with Python, Flask, SQLite, HTML, CSS and plain JavaScript. Book prices are stored as integer paise (for example, ₹199.00 is `19900`).

## Run locally

1. Open this folder in VS Code and use Python 3.10+.
2. Create and activate a virtual environment (recommended):
   - Windows: `python -m venv venv` then `venv\\Scripts\\activate`
3. Install the only runtime dependency: `pip install -r requirements.txt`.
4. Initialize/upgrade the database: `python app.py` (the app's `init_db()` creates any missing tables and indexes). Optional numbered SQL migrations can be run with `python migrate.py` after the database has been initialized.
5. Open `http://127.0.0.1:5000`.

Set `PAGETURNER_SECRET_KEY` to a stable random secret before deployment. Do not deploy Flask's development server or use debug mode in production.

## Main routes

- `/` — catalogue, category filter, title/author search, sort and pagination.
- `/book/<id>` — book detail and related-book suggestions.
- `/cart/add/<id>` — add a copy to the session cart (POST, CSRF checked, stock checked).
- `/cart` — current cart and subtotal.
- `/checkout` — validates customer details and places the complete cart in one SQLite transaction; reduces stock, saves each order item at its purchase price, and rolls back if any item is unavailable.
- `/orders` — order history page.
- `/order/<id>` — order detail/confirmation.
- `/order/<id>/invoice` — printable invoice; checks that the order belongs to the logged-in customer or is viewed by an admin.
- `/addresses` — manage saved shipping addresses.
- `/register`, `/login`, `/logout` — customer account routes. New passwords are hashed; existing legacy plaintext passwords are upgraded after a successful login.
- `/wishlist` — wishlist.
- `/admin/login`, `/admin/dashboard`, `/admin/inventory`, `/admin/coupons`, `/admin/analytics`, `/admin/reviews` — administration pages.
- `/api/books?q=python` — JSON catalogue search, optionally accepts `category` and `sort`.
- `/api/books/<id>` — JSON details, or a JSON 404 response.
- `/api/cart/add/<id>` — CSRF-protected JSON add-to-cart endpoint for JavaScript.

## Database additions

`order_items` stores multiple books per order and the price at purchase. `order_status_history` records order status events. `stock_log` records stock changes. `coupon_uses` tracks coupon usage. Indexes cover category and order owner lookups. The numbered SQL migration is in `migrations/` and `migrate.py` records applied versions.

## Notes on scope

The project now includes the key transaction-safe multi-item checkout, stock decrement, order-item persistence, API endpoints, password hashing for customer accounts, pagination, printable invoice and audit tables. The supplied feature notes contain many additional advanced/stretch features (for example, full role management, reservations, return workflow, shipping/tax rules, device sessions, CSV import, and comprehensive automated tests); these should be verified and implemented in separate steps before claiming the entire 74-step checklist is complete.


## Latest enhancement backlog implementation
- Admin dashboard can create percentage or fixed-value promotional coupons; customers can apply active codes at checkout.
- Staff demo login supports `Staff` / `123` (also `staff` / `staff123`). Staff can view the dashboard but inventory changes and CSV export return HTTP 403. Admin login remains `admin` / `admin123`.
- Successful checkout creates a notification and shows an animated confirmation tick.
- Book-card images use a landscape container and the inventory view uses a wider, more readable layout.
- Customer login includes Forgot Password. In this local demo, the reset link is displayed on screen because no email provider is configured; links expire after 30 minutes and are single-use.
- UPI/card payment methods remain demo selections only; no real payment gateway is configured.

### Final enhancements
- Admin Dashboard → Coupons shows saved coupons and supports editing and pausing/reactivating coupons (admin role only).
- Sales & Inventory Analysis includes interactive charts for sales/orders over time, title counts by category, and stock units by category. Charts load Chart.js from a CDN, so an internet connection is needed for the charts to render.
- The storefront category navigation and category filter are generated from categories currently present in the books table. Enter a new category in Admin → Inventory Control when adding or editing a book; it will then appear on the storefront. Search supports title/author and sort options include price and rating.


## Customer account lock / unlock

- Customer login locks an account for 15 minutes after five incorrect passwords. The lock is enforced by the server and stored in SQLite.
- An administrator can manually lock/unlock customer accounts at `/admin/customers` (sign in at `/admin/login` with an admin account). Staff cannot access this page.
- Unlocking clears failed attempts and any temporary lock. Existing user accounts are upgraded automatically when `init_db()` runs.
- The admin credentials currently hard-coded in this demo are for local development only; change them before deployment.

## Latest checkout / OTP / offers update (2026-10-26)
- Guest checkout status history now uses `guest` for the required `changed_by` value; signed-in orders store the user ID as text.
- Login now asks for a six-digit OTP after the password. For this local project demo, the code is printed in the VS Code terminal and expires in five minutes; no email/SMS delivery provider is configured.
- The homepage shows currently active flash-sale books based on `sale_price_paise`, `sale_start`, and `sale_end`; checkout applies active sale prices to the order total and order-item price. The old cart display may still show regular prices.
- A gift-voucher promotion panel is visible on the homepage. Gift voucher creation/redemption is not yet wired end-to-end.

Run from this folder with `python -m pip install -r requirements.txt` then `python app.py`. Preserve a backup of `database.db` before using a project database.
