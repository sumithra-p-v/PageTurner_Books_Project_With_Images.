-- Reset and re-seed books table
DELETE FROM books;

INSERT INTO books (title, author, category, price, description, image_url, stock)
VALUES 
('The Silent Garden', 'Maya Thomas', 'Fiction', 335.00, 'A captivating mystery novel.', 'https://covers.openlibrary.org/b/id/10523366-L.jpg', 12),
('The Last Letter', 'Ananya Kumar', 'Fiction', 299.00, 'A moving personal journey.', 'https://covers.openlibrary.org/b/id/12818862-L.jpg', 8),
('River of Dreams', 'Felix Joseph', 'Fiction', 450.00, 'An enchanting adventure story.', 'https://covers.openlibrary.org/b/id/8225261-L.jpg', 15),
('Python Made Simple', 'David Miller', 'Technology', 550.00, 'Comprehensive guide to Python programming.', 'https://covers.openlibrary.org/b/id/11100522-L.jpg', 5),
('Web Development Basics', 'Sarah Wilson', 'Technology', 499.00, 'Learn HTML, CSS, and JavaScript.', 'https://covers.openlibrary.org/b/id/8231996-L.jpg', 20),
('SQL for Beginners', 'Amit Patel', 'Technology', 425.00, 'Master relational databases easily.', 'https://covers.openlibrary.org/b/id/12539186-L.jpg', 7),
('Atomic Habits Guide', 'Ravi Verma', 'Self-Help', 550.00, 'Build good habits and break bad ones.', 'https://covers.openlibrary.org/b/id/10909258-L.jpg', 10),
('Think Better', 'Dr. Lisa Ray', 'Self-Help', 350.00, 'Tools for effective decision-making.', 'https://covers.openlibrary.org/b/id/9255566-L.jpg', 3);

-- Seed initial discount coupons (Step 21)
DELETE FROM coupons;

INSERT INTO coupons (code, percent, expiry_date, active) 
VALUES 
('WELCOME10', 10.0, '2030-12-31', 1),
('SAVE20', 20.0, '2030-12-31', 1);