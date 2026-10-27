# Feature 44 / Flash Sale schema fix

The prior ZIP contained storefront and checkout logic referring to `release_date`, but the bundled database and startup migration did not add that column. This update adds guarded, repeatable column migrations in `app.py` startup for `release_date`, `sale_price_paise`, `sale_start`, and `sale_end`. Existing book rows and user/order data are preserved. `schema.sql` is also updated for fresh databases.

## Apply
1. Back up `database.db`.
2. Open the inner `Page Turner app project` folder in VS Code.
3. Run `python app.py`. `init_db()` adds any missing columns automatically.
4. Check in SQLite: `PRAGMA table_info(books);` and confirm all four columns exist.
5. In admin Inventory Control, set a book's release date to today to test NEW, and tomorrow to test COMING SOON.

Do not manually execute `migrations/002_release_date_flash_sale.sql` if startup migration already ran; SQLite will report duplicate columns.
