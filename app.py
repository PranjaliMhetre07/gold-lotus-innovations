
import os
import re
import hmac
import sqlite3
import smtplib

from email.message import EmailMessage
from functools import wraps
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    session
)

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY", "")

if not app.secret_key or app.secret_key == "replace_with_a_long_random_secret":
    raise RuntimeError(
        "Set a strong, random FLASK_SECRET_KEY in your .env file."
    )

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "inquiries.db")

BUSINESS_EMAIL = os.getenv("BUSINESS_EMAIL", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "").replace(" ", "")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

ALLOWED_SERVICES = {
    "Custom Software Development",
    "Web Application Development",
    "AI-Based Projects",
    "Other"
}

ALLOWED_STATUSES = {"New", "In Progress", "Completed"}


def init_db():
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row

        connection.execute("""
            CREATE TABLE IF NOT EXISTS inquiries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                service TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(inquiries)"
            ).fetchall()
        }

        if "status" not in columns:
            connection.execute("""
                ALTER TABLE inquiries
                ADD COLUMN status TEXT NOT NULL DEFAULT 'New'
            """)


def send_inquiry_email(name, customer_email, service, details, inquiry_id):
    if not BUSINESS_EMAIL or not EMAIL_PASSWORD:
        app.logger.error("Gmail settings are missing in .env")
        return False

    message = EmailMessage()
    message["Subject"] = (
        f"New Customer Inquiry #{inquiry_id} - Gold Lotus Innovations"
    )
    message["From"] = BUSINESS_EMAIL
    message["To"] = BUSINESS_EMAIL
    message["Reply-To"] = customer_email

    message.set_content(f"""
A new inquiry has been submitted through the Gold Lotus Innovations website.

INQUIRY ID: {inquiry_id}
CUSTOMER NAME: {name}
CUSTOMER EMAIL: {customer_email}
SERVICE REQUIRED: {service}

PROJECT DETAILS:
{details}

Gold Lotus Innovations
""")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(BUSINESS_EMAIL, EMAIL_PASSWORD)
            server.send_message(message)

        app.logger.info("Notification sent for inquiry #%s", inquiry_id)
        return True

    except (smtplib.SMTPException, OSError):
        app.logger.exception("Could not send inquiry notification")
        return False


def admin_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Please log in to access the admin dashboard.", "error")
            return redirect(url_for("admin_login"))
        return view_function(*args, **kwargs)

    return wrapped_view


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/submit-inquiry", methods=["POST"])
def submit_inquiry():
    name = request.form.get("name", "").strip()
    customer_email = request.form.get("email", "").strip()
    service = request.form.get("service", "").strip()
    details = request.form.get("details", "").strip()

    if not name or not customer_email or not service or not details:
        flash("Please complete all fields.", "error")
        return redirect(url_for("home") + "#contact")

    if len(name) > 100 or len(customer_email) > 254 or len(details) > 5000:
        flash("Some information is too long.", "error")
        return redirect(url_for("home") + "#contact")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", customer_email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("home") + "#contact")

    if service not in ALLOWED_SERVICES:
        flash("Please select a valid service.", "error")
        return redirect(url_for("home") + "#contact")

    try:
        with sqlite3.connect(DATABASE) as connection:
            cursor = connection.execute("""
                INSERT INTO inquiries (name, email, service, details)
                VALUES (?, ?, ?, ?)
            """, (name, customer_email, service, details))
            inquiry_id = cursor.lastrowid

    except sqlite3.Error:
        app.logger.exception("Could not save inquiry")
        flash("We could not save your inquiry. Please try again later.", "error")
        return redirect(url_for("home") + "#contact")

    email_sent = send_inquiry_email(
        name, customer_email, service, details, inquiry_id
    )

    if email_sent:
        flash(
            "Thank you! Your inquiry was saved and an email notification was sent.",
            "success"
        )
    else:
        flash(
            "Your inquiry was saved, but its email notification could not be sent.",
            "success"
        )

    return redirect(url_for("home") + "#contact")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if not ADMIN_USERNAME or not ADMIN_PASSWORD:
            app.logger.error("Admin credentials are missing in .env")
            flash("Admin login is not configured. Check the .env file.", "error")
            return render_template("admin_login.html")

        username_ok = hmac.compare_digest(username, ADMIN_USERNAME)
        password_ok = hmac.compare_digest(password, ADMIN_PASSWORD)

        if username_ok and password_ok:
            session.clear()
            session["is_admin"] = True
            flash("Welcome to the admin dashboard.", "success")
            return redirect(url_for("admin_dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("admin_login.html")


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row

        total_inquiries = connection.execute(
            "SELECT COUNT(*) FROM inquiries"
        ).fetchone()[0]

        new_inquiries = connection.execute(
            "SELECT COUNT(*) FROM inquiries WHERE status = 'New'"
        ).fetchone()[0]

        in_progress = connection.execute(
            "SELECT COUNT(*) FROM inquiries WHERE status = 'In Progress'"
        ).fetchone()[0]

        completed = connection.execute(
            "SELECT COUNT(*) FROM inquiries WHERE status = 'Completed'"
        ).fetchone()[0]

        inquiries = connection.execute("""
            SELECT id, name, email, service, details, created_at, status
            FROM inquiries
            ORDER BY id DESC
        """).fetchall()

    return render_template(
        "admin_dashboard.html",
        total_inquiries=total_inquiries,
        new_inquiries=new_inquiries,
        in_progress=in_progress,
        completed=completed,
        inquiries=inquiries
    )


@app.route("/admin/update-status/<int:inquiry_id>", methods=["POST"])
@admin_required
def update_inquiry_status(inquiry_id):
    status = request.form.get("status", "")

    if status not in ALLOWED_STATUSES:
        flash("Invalid inquiry status.", "error")
        return redirect(url_for("admin_dashboard") + "#inquiries")

    with sqlite3.connect(DATABASE) as connection:
        cursor = connection.execute("""
            UPDATE inquiries
            SET status = ?
            WHERE id = ?
        """, (status, inquiry_id))

        if cursor.rowcount == 0:
            flash("Inquiry not found.", "error")
        else:
            flash("Inquiry status updated successfully.", "success")

    return redirect(url_for("admin_dashboard") + "#inquiries")


@app.route("/admin/logout", methods=["GET", "POST"])
def admin_logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
