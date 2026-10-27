# PageTurner Part 5 Update — Feature Notes

## Added in this update

- **Release dates (step 44):** inventory form accepts a release date. A future release date is shown as Coming Soon, hides the add-to-cart control, and is rejected again on the server during checkout. Books released within the previous 14 days show a New label.
- **Flash sale (step 47):** inventory form accepts a sale price and start/end times. Existing cart and checkout price calculation only applies a valid sale price during the configured window; order items save the effective price used for the invoice. Storefront shows a countdown to the sale end time when configured. Sale times are stored/compared as UTC-like timestamps; for this demo, enter them consistently with the server's UTC clock.
- **Gift wrap (step 48):** checkout checkbox adds ₹30, allows up to 150 characters, stores the gift message, and displays it on the order confirmation and invoice. Jinja autoescaping keeps HTML/script text as plain text.
- **Review moderation (step 51):** customers can report reviews; admins can hide or keep reported reviews in Admin Dashboard → Book Requests & Moderation. Hidden reviews are removed from the storefront. Policy: hidden reviews are excluded from public display and any rating averages.
- **Book requests (step 52):** customer form groups requests for the same case-insensitive title/author and increments the count. Admins see requests ordered by popularity and can approve (creates a draft book with zero stock; admin must set price/stock in Inventory Control) or reject.
- **Admin navigation:** new “Book Requests & Moderation” link on the dashboard.
- **Checkout history:** guest checkout writes a non-null `changed_by` value (`guest`) to order status history, avoiding the previous NOT NULL failure.

## Database and running

The app performs additive, repeatable SQLite migrations on startup. Back up `database.db` before testing or replacing project files. Run from the inner `Page Turner app project` directory:

```powershell
python -m pip install -r requirements.txt
python app.py
```

## Verification status

- `python -m py_compile app.py advanced_features.py` passes.
- Full Flask test-client/runtime verification was not possible in the build environment because Flask is not installed there. Please verify locally with the project's `requirements.txt` before using this as a production system.

## Part 6 follow-up: Welcome message and loyalty points
- The storefront top bar now greets signed-in customers by name and displays `Welcome, guest!` for visitors.
- Signed-in customers see their current loyalty-points balance on the home page.
- Loyalty points are added only after a successful signed-in checkout: 1 point per completed ₹100 of the final order total (amounts are stored in paise). Guest orders do not earn points.
- The order-success flash message and customer order notification state how many points were earned.
- A safe `users.loyalty_points` migration adds the field to existing databases without deleting users or orders.
- Verification limitation: Python syntax compilation passed; runtime route testing could not be completed in this environment because Flask is not installed here. Run with the project's normal `pip install -r requirements.txt` setup to verify in the browser.
