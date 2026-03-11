from flask import Flask, render_template, request, redirect, session
import qrcode, uuid, os
from datetime import datetime, timedelta
from pymongo import MongoClient
from bson import ObjectId

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

# ===================== CONFIG =====================
QR_FOLDER = "static/qr"
os.makedirs(QR_FOLDER, exist_ok=True)
active_qr = {}
pending_confirmations = {} # session_id -> {student_id: True}

# ===================== DATABASE =====================
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["smart_attendance"]
users_col = db["users"]
attendance_col = db["attendance"]
notify_col = db["notifications"]

# ===================== HOME =====================
@app.route("/")
def home():
    return render_template("index.html")

# ===================== LOGIN =====================
@app.route("/student-login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        user = users_col.find_one({
            "username": request.form["username"],
            "password": request.form["password"],
            "role": "student"
        })
        if user:
            session["user"] = user["username"]
            session["role"] = "student"
            return redirect("/student-dashboard")
        return render_template("student_login.html", error="Invalid credentials")
    return render_template("student_login.html")

@app.route("/teacher-login", methods=["GET", "POST"])
def teacher_login():
    if request.method == "POST":
        user = users_col.find_one({
            "username": request.form["username"],
            "password": request.form["password"],
            "role": "teacher"
        })
        if user:
            session["user"] = user["username"]
            session["role"] = "teacher"
            return redirect("/teacher-dashboard")
        return render_template("teacher_login.html", error="Invalid credentials")
    return render_template("teacher_login.html")

# ===================== REGISTER =====================
@app.route("/student-register", methods=["GET", "POST"])
def student_register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        name = request.form["name"].strip()
        if users_col.find_one({"username": username, "role": "student"}):
            return render_template("student_register.html", error="Username already exists")
        users_col.insert_one({"username": username, "password": password, "name": name, "role": "student"})
        session["user"] = username
        session["role"] = "student"
        return redirect("/student-dashboard")
    return render_template("student_register.html")

@app.route("/teacher-register", methods=["GET", "POST"])
def teacher_register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        name = request.form["name"].strip()
        if users_col.find_one({"username": username, "role": "teacher"}):
            return render_template("teacher_register.html", error="Username already exists")
        users_col.insert_one({"username": username, "password": password, "name": name, "role": "teacher"})
        session["user"] = username
        session["role"] = "teacher"
        return redirect("/teacher-dashboard")
    return render_template("teacher_register.html")


# ===================== DASHBOARDS =====================
@app.route("/student-dashboard")
def student_dashboard():
    if session.get("role") != "student":
        return redirect("/")
    
    student = session["user"]
    total = attendance_col.count_documents({"student": student})
    present = attendance_col.count_documents({"student": student, "status": "Present"})
    percentage = round((present / total) * 100, 2) if total else 0
    
    recent_records = attendance_col.find({"student": student}).sort("_id", -1).limit(5)
    
    return render_template("student_dashboard.html", 
                           total=total, 
                           present=present,
                           percentage=percentage,
                           recent_records=recent_records)

@app.route("/teacher-dashboard")
def teacher_dashboard():
    if session.get("role") != "teacher":
        return redirect("/")
    
    today = datetime.now().strftime("%Y-%m-%d")
    total_present = attendance_col.count_documents({"date": today, "status": "Present"})
    
    # Simple logic for active sessions (current active_qr)
    active_sessions = len(active_qr)
    
    # Recent logs for the table
    recent_logs = attendance_col.find({"date": today}).sort("_id", -1).limit(5)
    
    # Overall percentage today (dummy total student count of 50 for now, or just show count)
    return render_template("teacher_dashboard.html", 
                           total_present=total_present,
                           active_sessions=active_sessions,
                           recent_logs=recent_logs)

# ===================== GENERATE QR =====================
@app.route("/generate-qr", methods=["GET", "POST"])
def generate_qr():
    if session.get("role") != "teacher":
        return redirect("/")

    # Phase 2: Manual Trigger for Confirmation Code
    if request.method == "POST" and request.form.get("action") == "generate_confirm":
        entry_id = request.form.get("entry_id")
        if entry_id not in active_qr:
            return redirect("/generate-qr")
            
        confirm_id = str(uuid.uuid4())
        qr_data = active_qr[entry_id]
        
        # Add confirmation to active set
        active_qr[confirm_id] = {
            "type": "confirm",
            "entry_id": entry_id,
            "subject": qr_data["subject"],
            "qr_expiry": qr_data["qr_expiry"]
        }
        
        # Link entry to confirm
        active_qr[entry_id]["confirm_id"] = confirm_id
        
        # Generate Confirmation QR image
        qrcode.make(confirm_id).save(f"{QR_FOLDER}/{confirm_id}.png")
        
        return render_template(
            "generate_qr.html",
            entry_img=f"{entry_id}.png",
            confirm_img=f"{confirm_id}.png",
            subject=qr_data["subject"],
            step=2
        )

    # Phase 1: Initial Generation (Entry QR Only)
    if request.method == "POST":
        subject = request.form["subject"]
        class_start = request.form["class_start"]
        class_end = request.form["class_end"]
        valid_minutes = int(request.form.get("valid_minutes", 2))

        entry_id = str(uuid.uuid4())
        expiry = datetime.now() + timedelta(minutes=valid_minutes)

        active_qr.clear()
        pending_confirmations.clear()
        
        active_qr[entry_id] = {
            "type": "entry",
            "subject": subject,
            "class_start": class_start,
            "class_end": class_end,
            "qr_expiry": expiry
        }
        pending_confirmations[entry_id] = {}

        # Generate Entry QR image
        qrcode.make(entry_id).save(f"{QR_FOLDER}/{entry_id}.png")

        return render_template(
            "generate_qr.html",
            entry_img=f"{entry_id}.png",
            entry_id=entry_id,
            subject=subject,
            step=1
        )

    return render_template("generate_qr.html")

# ===================== SCAN QR =====================
@app.route("/scan-qr")
def scan_qr():
    if session.get("role") != "student":
        return redirect("/")

    if not active_qr:
        return redirect("/student-dashboard")

    qr_id = list(active_qr.keys())[0]
    qr = active_qr[qr_id]
    remaining = int((qr["qr_expiry"] - datetime.now()).total_seconds())

    if remaining <= 0:
        return redirect("/student-dashboard")

    return render_template("scan_qr.html", remaining_time=remaining)

# ===================== SCAN RESULT =====================
@app.route("/scan-result", methods=["POST"])
def scan_result():
    if session.get("role") != "student":
        return redirect("/")

    qr_id = request.form.get("session_id")
    student = session["user"]
    now = datetime.now()

    if qr_id not in active_qr:
        return render_template("scan_result.html", status="Invalid", message="Invalid QR Code.")

    qr = active_qr[qr_id]
    if now > qr["qr_expiry"]:
        return render_template("scan_result.html", status="Expired", message="This QR code has expired.")

    if qr["type"] == "entry":
        # Record entry
        entry_list = pending_confirmations.get(qr_id, {})
        entry_list[student] = True
        pending_confirmations[qr_id] = entry_list
        return render_template("scan_result.html", 
                               status="Entry Recorded", 
                               message="Entry recorded successfully. Please scan the Confirmation QR to complete the process.")

    elif qr["type"] == "confirm":
        # Verify sequence
        entry_id = qr["entry_id"]
        if student not in pending_confirmations.get(entry_id, {}):
            return render_template("scan_result.html", 
                                   status="Denied", 
                                   message="Please scan Entry QR first.")

        # Check for duplicates (if already present today)
        existing = attendance_col.find_one({
            "student": student,
            "subject": qr["subject"],
            "date": now.strftime("%Y-%m-%d"),
            "status": "Present"
        })
        if existing:
            return render_template("scan_result.html", 
                                   status="Duplicate", 
                                   message="You have already confirmed attendance for this session.")

        # Finalize attendance
        attendance_col.insert_one({
            "student": student,
            "subject": qr["subject"],
            "date": now.strftime("%Y-%m-%d"),
            "status": "Present",
            "scan_time": now.strftime("%H:%M")
        })
        
        # Cleanup pending
        if student in pending_confirmations[entry_id]:
            del pending_confirmations[entry_id][student]

        return render_template("scan_result.html", 
                               status="Success", 
                               message="Attendance confirmed successfully.")

    return redirect("/student-dashboard")

# ===================== STUDENT ATTENDANCE =====================
@app.route("/my-attendance")
def my_attendance():
    if session.get("role") != "student":
        return redirect("/")
    records = attendance_col.find({"student": session["user"]})
    return render_template("student_attendance.html", records=records)

# ===================== STUDENT ANALYTICS (ADVANCED) =====================
@app.route("/student-analytics")
def student_analytics():
    if session.get("role") != "student":
        return redirect("/")

    student = session["user"]
    total = attendance_col.count_documents({"student": student})
    present = attendance_col.count_documents({"student": student, "status": "Present"})
    absent = attendance_col.count_documents({"student": student, "status": "Absent"})
    percentage = round((present / total) * 100, 1) if total else 0

    # Trend: Last 7 days
    trend_data = []
    for i in range(6, -1, -1):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        count = attendance_col.count_documents({"student": student, "date": date, "status": "Present"})
        trend_data.append(count)

    # Subject Scorecard
    scorecard = list(attendance_col.aggregate([
        {"$match": {"student": student}},
        {"$group": {
            "_id": "$subject", 
            "total": {"$sum": 1},
            "attended": {"$sum": {"$cond": [{"$eq": ["$status", "Present"]}, 1, 0]}}
        }}
    ]))
    
    for s in scorecard:
        s["percent"] = round((s["attended"] / s["total"]) * 100, 1)
        s["health"] = "mint" if s["percent"] >= 75 else "yellow" if s["percent"] >= 60 else "purple"

    return render_template(
        "student_analytics.html",
        total=total,
        present=present,
        percentage=percentage,
        trend_data=trend_data,
        scorecard=scorecard,
        target_gap=max(0, 75 - percentage)
    )

# ===================== TEACHER ATTENDANCE =====================
@app.route("/today-attendance")
def today_attendance():
    if session.get("role") != "teacher":
        return redirect("/")
    today = datetime.now().strftime("%Y-%m-%d")
    records = attendance_col.find({"date": today})
    return render_template("today_attendance.html", records=records)

@app.route("/attendance-report")
def attendance_report():
    if session.get("role") != "teacher":
        return redirect("/")
    records = attendance_col.find()
    return render_template("attendance_report.html", records=records)

@app.route("/mark-absent/<rid>")
def mark_absent(rid):
    if session.get("role") != "teacher":
        return redirect("/")
    attendance_col.update_one({"_id": ObjectId(rid)}, {"$set": {"status": "Absent"}})
    return redirect("/today-attendance")

# ===================== TEACHER ANALYTICS (ADVANCED) =====================
@app.route("/analytics")
def analytics():
    if session.get("role") != "teacher":
        return redirect("/")

    today = datetime.now().strftime("%Y-%m-%d")
    total_today = attendance_col.count_documents({"date": today})
    present_today = attendance_col.count_documents({"date": today, "status": "Present"})
    percentage = round((present_today / total_today) * 100, 1) if total_today else 0

    # Weekly Trend
    trend_labels = []
    trend_values = []
    for i in range(6, -1, -1):
        date_obj = datetime.now() - timedelta(days=i)
        date_str = date_obj.strftime("%Y-%m-%d")
        trend_labels.append(date_obj.strftime("%a"))
        
        day_total = attendance_col.count_documents({"date": date_str})
        day_present = attendance_col.count_documents({"date": date_str, "status": "Present"})
        trend_values.append(round((day_present / day_total) * 100, 1) if day_total else 0)

    # Hourly Activity (Heatmap)
    hourly_data = [0] * 24
    scans = attendance_col.find({"date": today})
    for s in scans:
        try:
            hour = int(s["scan_time"].split(":")[0])
            if 0 <= hour < 24: hourly_data[hour] += 1
        except: continue

    # At-Risk Students (Attendance < 75%)
    # Logic: Group by student, calculate percentage
    all_stats = list(attendance_col.aggregate([
        {"$group": {
            "_id": "$student",
            "total": {"$sum": 1},
            "present": {"$sum": {"$cond": [{"$eq": ["$status", "Present"]}, 1, 0]}}
        }}
    ]))
    
    at_risk = []
    for s in all_stats:
        percent = round((s["present"] / s["total"]) * 100, 1)
        if percent < 75:
            at_risk.append({"id": s["_id"], "percent": percent, "count": s["total"]})

    return render_template(
        "teacher_analytics.html",
        percentage=percentage,
        trend_labels=trend_labels,
        trend_values=trend_values,
        hourly_data=hourly_data,
        at_risk=at_risk
    )

# ===================== NOTIFICATIONS =====================
@app.route("/notifications")
def notifications():
    if session.get("role") != "student":
        return redirect("/")
    notes = notify_col.find()
    return render_template("notifications.html", notes=notes)

@app.route("/post-notification", methods=["GET", "POST"])
def post_notification():
    if session.get("role") != "teacher":
        return redirect("/")
    if request.method == "POST":
        notify_col.insert_one({
            "title": request.form["title"],
            "message": request.form["message"],
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        return redirect("/post-notification")
    return render_template("post_notification.html")

# ===================== PROFILE =====================
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user" not in session:
        return redirect("/")
    user = users_col.find_one({"username": session["user"]})
    if request.method == "POST":
        users_col.update_one(
            {"username": session["user"]},
            {"$set": {"name": request.form["name"], "email": request.form["email"]}}
        )
        return redirect("/profile")
    return render_template("profile.html", user=user)

# ===================== LOGOUT =====================
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ===================== RUN =====================
if __name__ == "__main__":
    app.run(debug=True)
