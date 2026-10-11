import os
import re
import csv
import io
import sqlite3
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, Response
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "campussync_hackathon_eval_2026_secured"

ADMIN_PASSWORD = "admin"
UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
DATABASE = "complaints.db"

# -------------------------------------------------------------
# PASTE YOUR REAL FAST2SMS API KEY HERE:
# -------------------------------------------------------------
FAST2SMS_API_KEY = "9uf5tLY3vObgpKe2JaSGi4TjBxc7MXrhFyVQD8ENAI0HoCZmn6Qu5bmjBarNfs0D7g2GeI48zLn1OPM9".strip()

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT UNIQUE NOT NULL,
            student_name TEXT NOT NULL,
            student_id TEXT NOT NULL,
            phone_number TEXT,
            location TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            image_path TEXT,
            category TEXT NOT NULL,
            department TEXT NOT NULL,
            priority TEXT NOT NULL,
            confidence INTEGER DEFAULT 95,
            sla_target TEXT,
            status TEXT DEFAULT 'Submitted',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    cursor = conn.execute("PRAGMA table_info(complaints)")
    columns = [col[1] for col in cursor.fetchall()]
    if "phone_number" not in columns:
        conn.execute("ALTER TABLE complaints ADD COLUMN phone_number TEXT DEFAULT ''")

    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def send_real_sms(phone, tracking_id, action_type="REGISTERED"):
    """
    Dispatches SMS using Fast2SMS Quick Route ('q').
    Branded under CampusSync Helpdesk.
    """
    clean_phone = re.sub(r"\D", "", str(phone))
    if len(clean_phone) > 10:
        clean_phone = clean_phone[-10:]

    key = FAST2SMS_API_KEY.strip()
    if not key or "PASTE_YOUR" in key:
        print(f"⚠️ [SIMULATION] To: {clean_phone} | Token: {tracking_id} | Type: {action_type}")
        return

    # Dynamic message text
    if action_type == "RESOLVED CLOSURE":
        msg = f"CampusSync Helpdesk: Your grievance {tracking_id} has been RESOLVED by maintenance. Thank you!"
    else:
        msg = f"CampusSync Helpdesk: Your grievance {tracking_id} is registered and dispatched to department."

    url = "https://www.fast2sms.com/dev/bulkV2"
    headers = {
        "authorization": key,
        "Content-Type": "application/x-www-form-urlencoded",
        "cache-control": "no-cache"
    }
    data = {
        "route": "q",
        "message": msg,
        "language": "english",
        "flash": "0",
        "numbers": clean_phone
    }

    try:
        response = requests.post(url, data=data, headers=headers, timeout=8)
        res_data = response.json()
        print("\n" + "=" * 65)
        print("📡 [FAST2SMS QUICK ROUTE DISPATCH — CAMPUSSYNC]")
        print(f"📱 Sent To: +91 {clean_phone}")
        print(f"💬 Message: {msg}")
        print(f"📡 API Server Response: {res_data}")
        print("=" * 65 + "\n")
    except Exception as e:
        print(f"❌ SMS Gateway Request Error: {e}")


def intelligent_triage_engine(text):
    clean_text = text.lower()

    dept_matrix = {
        "Wi-Fi & IT Infrastructure": {
            "dept": "Department of IT & Network Administration",
            "keywords": ["wifi", "wi-fi", "internet", "router", "lan", "slow", "network", "server", "portal", "login", "ethernet", "mac", "ip", "bandwidth"]
        },
        "Electrical & Power Safety": {
            "dept": "Electrical Maintenance Division",
            "keywords": ["fan", "light", "switch", "short circuit", "spark", "plug", "socket", "power", "ac", "air conditioner", "wire", "blackout", "shock", "generator"]
        },
        "Sanitation & Public Health": {
            "dept": "Sanitation & Housekeeping Dept",
            "keywords": ["washroom", "toilet", "dustbin", "garbage", "trash", "smell", "water", "leakage", "dirty", "pest", "drain", "stink", "flush", "hygiene"]
        },
        "Classroom Infrastructure & Civil": {
            "dept": "Estate & Civil Works Division",
            "keywords": ["bench", "chair", "desk", "blackboard", "whiteboard", "door", "window", "crack", "wall", "projector", "roof", "broken", "table", "lift", "podium"]
        }
    }

    critical_hazards = ["shock", "spark", "fire", "danger", "urgent", "immediate", "flood", "smoke", "collapse", "hazard", "emergency", "exposed"]
    moderate_hazards = ["exam", "leakage", "presentation", "dark", "no internet", "broken", "stuck"]

    detected_cat = "General Campus Services"
    assigned_dept = "Central Administrative Cell"
    max_score = 0
    total_tokens_matched = 0

    for cat_name, meta in dept_matrix.items():
        score = sum(bool(re.search(r"\b" + re.escape(w) + r"\b", clean_text)) for w in meta["keywords"])
        total_tokens_matched += score
        if score > max_score:
            max_score = score
            detected_cat = cat_name
            assigned_dept = meta["dept"]

    priority = "Normal"
    sla_hours = 48

    if any(re.search(r"\b" + re.escape(w) + r"\b", clean_text) for w in critical_hazards):
        priority = "Urgent"
        sla_hours = 4
    elif any(re.search(r"\b" + re.escape(w) + r"\b", clean_text) for w in moderate_hazards) or max_score >= 2:
        priority = "High"
        sla_hours = 12

    confidence = min(98, max(82, 80 + (total_tokens_matched * 5))) if max_score > 0 else 75
    target_time = (datetime.now() + timedelta(hours=sla_hours)).strftime("%d %b %Y, %I:%M %p")

    return detected_cat, assigned_dept, priority, confidence, target_time


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/submit", methods=["POST"])
def submit_complaint():
    student_name = request.form.get("student_name", "").strip()
    student_id = request.form.get("student_id", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    location = request.form.get("location", "").strip()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()

    tracking_id = f"CMP-{datetime.now().strftime('%Y%m%d%H%M%S')[-8:]}"

    image_filename = None
    if "photo" in request.files:
        file = request.files["photo"]
        if file and allowed_file(file.filename):
            image_filename = secure_filename(f"{tracking_id}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))

    category, department, priority, confidence, sla_target = intelligent_triage_engine(f"{title} {description}")

    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO complaints (tracking_id, student_name, student_id, phone_number, location, title, description, image_path, category, department, priority, confidence, sla_target)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (tracking_id, student_name, student_id, phone_number, location, title, description, image_filename, category, department, priority, confidence, sla_target),
    )
    conn.commit()
    conn.close()

    # Automatic CampusSync SMS dispatched on submission
    if phone_number:
        send_real_sms(phone_number, tracking_id, action_type="REGISTRATION DISPATCH")

    return redirect(url_for("track_complaint", tracking_id=tracking_id))


@app.route("/track")
@app.route("/track/<tracking_id>")
def track_complaint(tracking_id=None):
    search_query = request.args.get("search_id", tracking_id)
    complaint = None
    if search_query:
        conn = get_db_connection()
        complaint = conn.execute("SELECT * FROM complaints WHERE tracking_id = ?", (search_query.strip(),)).fetchone()
        conn.close()
    return render_template("track.html", complaint=complaint, query=search_query)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))
        else:
            error = "Invalid administrator password. Access denied."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("login"))


@app.route("/admin")
def admin():
    if not session.get("admin_logged_in"):
        return redirect(url_for("login"))

    conn = get_db_connection()
    # 1. Unresolved complaints (Submitted / In Progress) pinned at top, Resolved at bottom
    # 2. Within each group, Urgent hazards appear first, followed by High, then Normal
    complaints = conn.execute("""
        SELECT * FROM complaints 
        ORDER BY 
            CASE 
                WHEN status = 'Resolved' THEN 2 
                ELSE 1 
            END ASC,
            CASE priority 
                WHEN 'Urgent' THEN 1 
                WHEN 'High' THEN 2 
                ELSE 3 
            END ASC, 
            id DESC
    """).fetchall()
    
    total = len(complaints)
    urgent_count = sum(1 for c in complaints if c["priority"] == "Urgent" and c["status"] != "Resolved")
    resolved_count = sum(1 for c in complaints if c["status"] == "Resolved")
    in_progress_count = sum(1 for c in complaints if c["status"] == "In Progress")
    rate = round((resolved_count / total * 100)) if total > 0 else 100

    conn.close()
    return render_template(
        "admin.html", 
        complaints=complaints, 
        stats={"total": total, "urgent": urgent_count, "resolved": resolved_count, "in_progress": in_progress_count, "rate": rate}
    )


@app.route("/update-status/<int:complaint_id>", methods=["POST"])
def update_status(complaint_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("login"))

    new_status = request.form.get("status")
    conn = get_db_connection()
    
    complaint = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    
    conn.execute("UPDATE complaints SET status = ? WHERE id = ?", (new_status, complaint_id))
    conn.commit()

    # Automatic CampusSync SMS dispatched immediately when marked 'Resolved'
    if new_status == "Resolved" and complaint and complaint["phone_number"]:
        send_real_sms(complaint["phone_number"], complaint["tracking_id"], action_type="RESOLVED CLOSURE")

    conn.close()
    return redirect(url_for("admin"))


@app.route("/export-csv")
def export_csv():
    if not session.get("admin_logged_in"):
        return redirect(url_for("login"))

    conn = get_db_connection()
    complaints = conn.execute("SELECT * FROM complaints ORDER BY id DESC").fetchall()
    conn.close()

    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(["Tracking ID", "Roll ID", "Student Name", "Phone No", "Location", "Title", "Category", "Department", "Priority", "Confidence %", "SLA Target", "Status", "Timestamp"])

    for row in complaints:
        writer.writerow([row["tracking_id"], row["student_id"], row["student_name"], row["phone_number"] or "N/A", row["location"], row["title"], row["category"], row["department"], row["priority"], f"{row['confidence']}%", row["sla_target"], row["status"], row["created_at"]])

    output = Response(si.getvalue(), mimetype="text/csv")
    output.headers["Content-Disposition"] = f"attachment; filename=CampusSync_Audit_{datetime.now().strftime('%Y%m%d')}.csv"
    return output


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)