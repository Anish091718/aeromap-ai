
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import uuid

from functools import wraps
from io import BytesIO

from flask import (
    Flask, Response, jsonify, redirect, render_template, request,
    send_file, send_from_directory, session, url_for
)

from openpyxl import Workbook

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

from werkzeug.security import (
    check_password_hash, generate_password_hash
)
from werkzeug.utils import secure_filename

import config

from db import db, init_db
from drone_live import get_hub
from gps_reader import get_gps_info
from odm_processor import dataset_dir
from pipeline import (
    is_running,
    start_processing,
    start_tiles_only
)


# ==========================================
# FLASK CONFIGURATION
# ==========================================

app = Flask(__name__)

app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["UPLOAD_FOLDER"] = str(config.UPLOAD_FOLDER)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "0") == "1"
)

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".jfif", ".tif", ".tiff"
}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Initialize database
init_db()


# ==========================================
# AUTHENTICATION HELPERS
# ==========================================

def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


def owned_project_exists(conn, project_id):

    return conn.execute(
        """
        SELECT 1 FROM projects
        WHERE id=? AND owner_id=?
        """,
        (project_id, session["user_id"])
    ).fetchone() is not None


def safe_upload_name(original_name):

    cleaned = secure_filename(original_name or "")

    suffix = os.path.splitext(cleaned)[1].lower()

    if not cleaned or suffix not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    if suffix == ".jfif":
        suffix = ".jpg"

    stem = os.path.splitext(cleaned)[0][:80] or "image"

    return f"{stem}_{uuid.uuid4().hex[:10]}{suffix}"



# ==========================================
# HOME
# ==========================================

@app.route("/")
def home():

    return render_template("index.html")


# ==========================================
# LOGIN
# ==========================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with db() as conn:

            user = conn.execute(
                """
                SELECT id, username, password
                FROM users
                WHERE email=?
                """,
                (email,)
            ).fetchone()

        if user and check_password_hash(user[2], password):

            session.clear()

            session["user_id"] = user[0]
            session["username"] = user[1]

            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid email or password."), 401

    return render_template("login.html")


# ==========================================
# SIGNUP
# ==========================================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or len(username) > 80:
            return render_template("signup.html", error="Enter a username (max 80 characters)."), 400

        if not email or len(email) > 254:
            return render_template("signup.html", error="Enter a valid email address."), 400

        if len(password) < 8:
            return render_template("signup.html", error="Password must contain at least 8 characters."), 400

        try:

            with db() as conn:

                conn.execute(
                    """
                    INSERT INTO users(username, email, password)
                    VALUES (?, ?, ?)
                    """,
                    (
                        username,
                        email,
                        generate_password_hash(password)
                    )
                )

        except sqlite3.IntegrityError:

            return render_template("signup.html", error="This email is already registered."), 409

        return redirect(url_for("login"))

    return render_template("signup.html")


# ==========================================
# LOGOUT
# ==========================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("home"))


# ==========================================
# DASHBOARD
# ==========================================

@app.route("/dashboard")
@login_required
def dashboard():

    user_id = session["user_id"]

    with db() as conn:

        stats = {

            "projects": conn.execute(
                """
                SELECT COUNT(*)
                FROM projects
                WHERE owner_id=?
                """,
                (user_id,)
            ).fetchone()[0],

            "images": conn.execute(
                """
                SELECT COUNT(*)
                FROM project_images i
                JOIN projects p ON p.id=i.project_id
                WHERE p.owner_id=?
                """,
                (user_id,)
            ).fetchone()[0],

            "measurements": conn.execute(
                """
                SELECT COUNT(*)
                FROM measurements m
                JOIN projects p ON p.id=m.project_id
                WHERE p.owner_id=?
                """,
                (user_id,)
            ).fetchone()[0],

            "completed": conn.execute(
                """
                SELECT COUNT(*)
                FROM projects
                WHERE status='Completed'
                AND owner_id=?
                """,
                (user_id,)
            ).fetchone()[0]
        }

        recent = conn.execute(
            """
            SELECT id, project_name, status
            FROM projects
            WHERE owner_id=?
            ORDER BY id DESC
            LIMIT 5
            """,
            (user_id,)
        ).fetchall()

    return render_template(
        "dashboard.html",
        stats=stats,
        recent=recent
    )


# ==========================================
# PROJECTS
# ==========================================

@app.route("/projects", methods=["GET", "POST"])
@login_required
def projects():

    user_id = session["user_id"]

    with db() as conn:

        if request.method == "POST":

            project_name = request.form.get(
                "project_name", ""
            ).strip()

            location = request.form.get(
                "location", ""
            ).strip()

            description = request.form.get(
                "description", ""
            ).strip()

            if not project_name:
                return "Project name is required.", 400

            conn.execute(
                """
                INSERT INTO projects
                (project_name, location, description, owner_id)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_name,
                    location,
                    description,
                    user_id
                )
            )

            return redirect(url_for("projects"))

        rows = conn.execute(
            """
            SELECT id, project_name, location, created_at
            FROM projects
            WHERE owner_id=?
            ORDER BY id DESC
            """,
            (user_id,)
        ).fetchall()

    return render_template(
        "projects.html",
        projects=rows
    )


# ==========================================
# DELETE PROJECT
# ==========================================

@app.route("/delete_project/<int:id>", methods=["POST"])
@login_required
def delete_project(id):

    with db() as conn:

        conn.execute(
            """
            DELETE FROM projects
            WHERE id=? AND owner_id=?
            """,
            (id, session["user_id"])
        )

        deleted = conn.execute(
            "SELECT changes()"
        ).fetchone()[0]

    if not deleted:
        return "Project Not Found", 404

    folders = [
        config.UPLOAD_FOLDER / f"project_{id}",
        config.ORTHO_ROOT / f"project_{id}",
        dataset_dir(id)
    ]

    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)

    return redirect(url_for("projects"))


# ==========================================
# PROJECT DETAILS
# ==========================================

@app.route("/project/<int:project_id>", methods=["GET", "POST"])
@login_required
def project_details(project_id):

    with db() as conn:

        project = conn.execute(
            """
            SELECT id, project_name, location, description,
                   created_at, status, progress
            FROM projects
            WHERE id=? AND owner_id=?
            """,
            (project_id, session["user_id"])
        ).fetchone()

        if project is None:
            return "Project Not Found", 404

        project_folder = (
            config.UPLOAD_FOLDER / f"project_{project_id}"
        )

        if request.method == "POST":

            project_folder.mkdir(
                parents=True,
                exist_ok=True
            )

            for file in request.files.getlist("images"):

                if not file.filename:
                    continue

                filename = safe_upload_name(file.filename)

                if not filename:
                    continue

                file_path = project_folder / filename

                file.save(file_path)

                try:
                    gps_data = get_gps_info(file_path)
                    print("GPS Data:", gps_data)

                except Exception as error:
                    print("GPS read failed:", error)

                conn.execute(
                    """
                    INSERT INTO project_images
                    (project_id, image_name)
                    VALUES (?, ?)
                    """,
                    (project_id, filename)
                )

        if request.method == "POST":
            return redirect(url_for("project_details", project_id=project_id))

        images = conn.execute(
            """
            SELECT image_name
            FROM project_images
            WHERE project_id=?
            ORDER BY id DESC
            """,
            (project_id,)
        ).fetchall()

    total_size = 0

    if project_folder.exists():

        total_size = sum(
            f.stat().st_size
            for f in project_folder.iterdir()
            if f.is_file()
        )

    return render_template(
        "project.html",
        project=project,
        images=images,
        image_count=len(images),
        total_size=round(
            total_size / (1024 * 1024), 2
        ),
        has_map=load_ortho_info(project_id) is not None
    )


# ==========================================
# LEGACY UPLOAD PAGE
# ==========================================

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():

    user_upload_folder = (
        config.UPLOAD_FOLDER /
        f"user_{session['user_id']}"
    )

    user_upload_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    if request.method == "POST":

        for file in request.files.getlist("images"):

            if not file.filename:
                continue

            filename = safe_upload_name(file.filename)

            if filename:
                file.save(user_upload_folder / filename)

        return redirect(url_for("upload"))

    images = [
        f.name
        for f in user_upload_folder.iterdir()
        if f.is_file()
    ]

    return render_template(
        "upload.html",
        images=images
    )


# ==========================================
# DELETE LEGACY IMAGE
# ==========================================

@app.route("/delete/<filename>", methods=["POST"])
@login_required
def delete_image(filename):

    user_upload_folder = (
        config.UPLOAD_FOLDER /
        f"user_{session['user_id']}"
    )

    safe_name = secure_filename(filename)

    file_path = user_upload_folder / safe_name

    if file_path.is_file():
        file_path.unlink()

    return redirect(url_for("upload"))


# ==========================================
# DELETE PROJECT IMAGE
# ==========================================

@app.route("/delete_image/<int:project_id>/<filename>", methods=["POST"])
@login_required
def delete_project_image(project_id, filename):

    filename = secure_filename(filename)

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return "Project Not Found", 404

        conn.execute(
            """
            DELETE FROM project_images
            WHERE project_id=? AND image_name=?
            """,
            (project_id, filename)
        )

    image_path = (
        config.UPLOAD_FOLDER /
        f"project_{project_id}" /
        filename
    )

    if image_path.exists():
        image_path.unlink()

    return redirect(
        url_for(
            "project_details",
            project_id=project_id
        )
    )


# ==========================================
# GENERATE ORTHOMOSAIC USING ODM
# ==========================================

@app.route("/generate/<int:project_id>", methods=["POST"])
@login_required
def generate_orthomosaic(project_id):

    with db() as conn:

        exists = owned_project_exists(
            conn, project_id
        )

    if not exists:
        return "Project Not Found", 404

    if is_running(project_id):
        return "Processing is already running.", 409

    started = start_processing(project_id)

    if not started:
        return "Processing is already running.", 409

    return redirect(
        url_for(
            "project_details",
            project_id=project_id
        )
    )


# ==========================================
# PROJECT PROCESSING STATUS
# ==========================================

@app.route("/status/<int:project_id>")
@login_required
def project_status(project_id):

    with db() as conn:

        row = conn.execute(
            """
            SELECT status, progress
            FROM projects
            WHERE id=? AND owner_id=?
            """,
            (project_id, session["user_id"])
        ).fetchone()

    if row is None:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "status": row[0],
        "progress": row[1],
        "running": is_running(project_id)
    })


# ==========================================
# ORTHOMOSAIC INFORMATION
# ==========================================

def load_ortho_info(project_id):

    info_path = (
        config.ORTHO_ROOT /
        f"project_{project_id}" /
        "tiles" /
        "bounds.json"
    )

    if not info_path.exists():
        return None

    info = json.loads(
        info_path.read_text(encoding="utf-8")
    )

    info["tiles_url"] = (
        f"/static/orthomosaic/project_{project_id}"
        "/tiles/{z}/{x}/{y}.png"
    )

    return info


# ==========================================
# ORTHOMOSAIC PAGE
# ==========================================

@app.route("/orthomosaic/<int:project_id>")
@login_required
def orthomosaic(project_id):

    with db() as conn:

        project = conn.execute(
            """
            SELECT id, project_name, location,
                   description, created_at
            FROM projects
            WHERE id=? AND owner_id=?
            """,
            (project_id, session["user_id"])
        ).fetchone()

        if project is None:
            return "Project Not Found", 404

        image_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM project_images
            WHERE project_id=?
            """,
            (project_id,)
        ).fetchone()[0]

    return render_template(
        "orthomosaic.html",
        project=project,
        image_count=image_count
    )


# ==========================================
# DOWNLOAD ORTHOMOSAIC
# ==========================================

@app.route("/orthomosaic-file/<int:project_id>")
@login_required
def orthomosaic_file(project_id):

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return "Project Not Found", 404

    folder = (
        config.ORTHO_ROOT /
        f"project_{project_id}"
    )

    file_path = folder / "orthomosaic.tif"

    if not file_path.exists():
        return "Orthomosaic not generated yet", 404

    return send_from_directory(
        folder,
        "orthomosaic.tif",
        as_attachment=True
    )


# ==========================================
# 2D MAP VIEWER
# ==========================================

@app.route("/map-viewer")
@app.route("/map-viewer/<int:project_id>")
@login_required
def map_viewer(project_id=None):

    with db() as conn:

        all_projects = conn.execute(
            """
            SELECT id, project_name, location
            FROM projects
            WHERE owner_id=?
            ORDER BY id DESC
            """,
            (session["user_id"],)
        ).fetchall()

        selected_project = None

        if project_id is not None:

            selected_project = conn.execute(
                """
                SELECT id, project_name, location
                FROM projects
                WHERE id=? AND owner_id=?
                """,
                (project_id, session["user_id"])
            ).fetchone()

    if project_id is not None and selected_project is None:
        return "Project Not Found", 404

    info = (
        load_ortho_info(project_id)
        if selected_project else None
    )

    return render_template(
        "map_viewer.html",
        projects=all_projects,
        selected_project=selected_project,
        orthomosaic_bounds=info["bounds"] if info else None,
        tiles_url=info["tiles_url"] if info else None,
        min_zoom=info["min_zoom"] if info else None,
        max_zoom=info["max_zoom"] if info else None
    )


# ==========================================
# 3D VIEWER
# ==========================================

@app.route("/3d-viewer")
@login_required
def viewer_3d():

    return render_template("3d_viewer.html")


# ==========================================
# UPLOAD READY ORTHOMOSAIC
# ==========================================

@app.route(
    "/upload-ortho/<int:project_id>",
    methods=["POST"]
)
@login_required
def upload_ortho(project_id):

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return "Project Not Found", 404

    file = request.files.get("ortho")

    if not file or not file.filename.lower().endswith(
        (".tif", ".tiff")
    ):
        return "Please choose a .tif / .tiff GeoTIFF file.", 400

    if is_running(project_id):
        return "Processing is already running.", 409

    folder = (
        config.ORTHO_ROOT /
        f"project_{project_id}"
    )

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    file.save(folder / "orthomosaic.tif")

    started = start_tiles_only(project_id)

    if not started:
        return "Processing is already running.", 409

    return redirect(
        url_for(
            "project_details",
            project_id=project_id
        )
    )


# ==========================================
# MEASUREMENTS API
# ==========================================

@app.route("/api/measurements/<int:project_id>")
@login_required
def api_measurements(project_id):

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return jsonify({"error": "not found"}), 404

        rows = conn.execute(
            """
            SELECT id, plot_name, area_sqm, coordinates
            FROM measurements
            WHERE project_id=?
            ORDER BY id
            """,
            (project_id,)
        ).fetchall()

    return jsonify([
        {
            "id": row[0],
            "plot_name": row[1],
            "area_sqm": row[2],
            "coordinates": json.loads(row[3] or "[]")
        }
        for row in rows
    ])


# ==========================================
# VIEW MEASUREMENTS
# ==========================================

@app.route("/measurements/<int:project_id>")
@login_required
def view_measurements(project_id):

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return "Project Not Found", 404

        rows = conn.execute(
            """
            SELECT id, plot_name, area_sqm,
                   area_hectares, area_acres, created_at
            FROM measurements
            WHERE project_id=?
            ORDER BY id DESC
            """,
            (project_id,)
        ).fetchall()

    return render_template(
        "measurements.html",
        project_id=project_id,
        measurements=rows
    )


# ==========================================
# SAVE MEASUREMENT
# ==========================================

@app.route(
    "/save-measurement/<int:project_id>",
    methods=["POST"]
)
@login_required
def save_measurement(project_id):

    data = request.get_json(silent=True) or {}

    plot_name = str(
        data.get("plot_name", "")
    ).strip()

    coordinates = data.get("coordinates")

    area_sqm = data.get("area_sqm")

    if (
        not plot_name
        or not isinstance(coordinates, list)
        or len(coordinates) < 3
        or area_sqm is None
    ):
        return jsonify({
            "success": False,
            "message": "Plot name, coordinates and area are required."
        }), 400

    try:

        area_sqm = float(area_sqm)

        if not (0 < area_sqm < float("inf")):
            raise ValueError

        if not all(
            isinstance(point, (list, tuple))
            and len(point) >= 2
            and all(
                isinstance(value, (int, float))
                and float("-inf") < value < float("inf")
                for value in point[:2]
            )
            for point in coordinates
        ):
            raise ValueError

    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid area or coordinates."
        }), 400

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return jsonify({
                "success": False,
                "message": "Project not found."
            }), 404

        conn.execute(
            """
            INSERT INTO measurements
            (
                project_id,
                plot_name,
                area_sqm,
                area_hectares,
                area_acres,
                coordinates
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                plot_name,
                area_sqm,
                area_sqm / 10000,
                area_sqm / 4046.8564224,
                json.dumps(coordinates)
            )
        )

    return jsonify({
        "success": True,
        "message": "Measurement saved successfully."
    })


# ==========================================
# DELETE MEASUREMENT
# ==========================================

@app.route(
    "/delete-measurement/<int:project_id>/<int:measurement_id>",
    methods=["POST"]
)
@login_required
def delete_measurement(project_id, measurement_id):

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return "Project Not Found", 404

        conn.execute(
            """
            DELETE FROM measurements
            WHERE id=? AND project_id=?
            """,
            (measurement_id, project_id)
        )

    return redirect(
        url_for(
            "view_measurements",
            project_id=project_id
        )
    )


# ==========================================
# FORMAT MEASUREMENT VALUES
# ==========================================

def _fmt(value, digits):

    if value is None:
        return ""

    return f"{value:,.{digits}f}"


# ==========================================
# FETCH MEASUREMENTS
# ==========================================

def _fetch_measurements(project_id):

    with db() as conn:

        if not owned_project_exists(conn, project_id):
            return None

        return conn.execute(
            """
            SELECT plot_name, area_sqm,
                   area_hectares, area_acres, created_at
            FROM measurements
            WHERE project_id=?
            ORDER BY id DESC
            """,
            (project_id,)
        ).fetchall()


# ==========================================
# EXPORT PDF REPORT
# ==========================================

@app.route(
    "/export-measurements/<int:project_id>/pdf"
)
@login_required
def export_measurements_pdf(project_id):

    measurements = _fetch_measurements(project_id)

    if measurements is None:
        return "Project Not Found", 404

    output = BytesIO()

    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        title="AeroMap AI - Measurement Report",
        author="AeroMap AI"
    )

    styles = getSampleStyleSheet()

    elements = [
        Paragraph(
            "AeroMap AI - Measurement Report",
            styles["Title"]
        ),
        Spacer(1, 20)
    ]

    data = [
        [
            "Plot Name",
            "Area (sqm)",
            "Hectares",
            "Acres",
            "Created At"
        ]
    ]

    for m in measurements:

        data.append([
            m[0],
            _fmt(m[1], 2),
            _fmt(m[2], 4),
            _fmt(m[3], 4),
            str(m[4])
        ])

    table = Table(
        data,
        repeatRows=1
    )

    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.darkgreen),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("PADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")
        ])
    )

    elements.append(table)

    document.build(elements)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"project_{project_id}_measurements.pdf",
        mimetype="application/pdf"
    )


# ==========================================
# EXPORT EXCEL REPORT
# ==========================================

@app.route(
    "/export-measurements/<int:project_id>/excel"
)
@login_required
def export_measurements_excel(project_id):

    measurements = _fetch_measurements(project_id)

    if measurements is None:
        return "Project Not Found", 404

    wb = Workbook()

    ws = wb.active
    ws.title = "Measurements"

    ws.append([
        "Plot Name",
        "Area (sqm)",
        "Hectares",
        "Acres",
        "Created At"
    ])

    for m in measurements:

        ws.append([
            m[0],
            round(m[1] or 0, 2),
            round(m[2] or 0, 4),
            round(m[3] or 0, 4),
            m[4]
        ])

    output = BytesIO()

    wb.save(output)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"project_{project_id}_measurements.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )


# ==========================================
# LIVE DRONE TRACKING
# ==========================================

def make_drone_token(user_id):
    """Stateless API token so external apps can push telemetry without a login session."""
    sig = hmac.new(
        config.SECRET_KEY.encode(),
        f"drone:{user_id}".encode(),
        hashlib.sha256
    ).hexdigest()[:32]

    return f"{user_id}.{sig}"


def user_from_drone_token(token):
    try:
        user_id, _ = token.split(".", 1)
        user_id = int(user_id)
    except (ValueError, AttributeError):
        return None

    if hmac.compare_digest(make_drone_token(user_id), token):
        return user_id

    return None


@app.route("/live")
@login_required
def live_view():

    project_id = request.args.get("project", type=int)

    with db() as conn:

        all_projects = conn.execute(
            """
            SELECT id, project_name
            FROM projects
            WHERE owner_id=?
            ORDER BY id DESC
            """,
            (session["user_id"],)
        ).fetchall()

    selected = next(
        (p for p in all_projects if p["id"] == project_id),
        None
    )

    info = load_ortho_info(project_id) if selected else None

    cfg = {
        "bounds": info["bounds"] if info else None,
        "tiles_url": info["tiles_url"] if info else None,
        "min_zoom": info["min_zoom"] if info else None,
        "max_zoom": info["max_zoom"] if info else None
    }

    return render_template(
        "live.html",
        projects=all_projects,
        selected_project=selected,
        cfg=cfg,
        token=make_drone_token(session["user_id"]),
        base_url=request.host_url.rstrip("/")
    )


@app.route("/api/drone/connect", methods=["POST"])
@login_required
def drone_connect():

    data = request.get_json(silent=True) or {}

    try:
        get_hub(session["user_id"]).connect(
            data.get("source"),
            connection=data.get("connection", ""),
            lat=data.get("lat"),
            lon=data.get("lon")
        )

    except ValueError as error:
        return jsonify({"ok": False, "message": str(error)}), 400

    return jsonify({"ok": True})


@app.route("/api/drone/disconnect", methods=["POST"])
@login_required
def drone_disconnect():

    get_hub(session["user_id"]).disconnect()

    return jsonify({"ok": True})


@app.route("/api/drone/telemetry")
@login_required
def drone_telemetry():

    since = request.args.get("since", 0, type=int)

    return jsonify(get_hub(session["user_id"]).snapshot(since))


@app.route("/api/drone/push", methods=["POST"])
def drone_push():

    user_id = session.get("user_id")

    if user_id is None:

        header = request.headers.get("Authorization", "")

        token = (
            header[7:] if header.startswith("Bearer ")
            else request.headers.get("X-Drone-Token", "")
        )

        user_id = user_from_drone_token(token)

    if user_id is None:
        return jsonify({"ok": False, "message": "Invalid or missing token."}), 401

    try:
        get_hub(user_id).push(request.get_json(silent=True))

    except ValueError as error:
        return jsonify({"ok": False, "message": str(error)}), 400

    return jsonify({"ok": True})


@app.route("/api/drone/track.geojson")
@login_required
def drone_track_geojson():

    return Response(
        json.dumps(get_hub(session["user_id"]).geojson()),
        mimetype="application/geo+json",
        headers={
            "Content-Disposition": "attachment; filename=drone_track.geojson"
        }
    )


# ==========================================
# MAIN
# ==========================================

if __name__ == "__main__":

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))

    # The interactive debugger is only enabled when the server is local-only.
    app.run(
        host=host,
        port=port,
        debug=host in ("127.0.0.1", "localhost"),
        use_reloader=False,
        threaded=True
    )
