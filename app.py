import json
import os
import shutil
from functools import wraps
from io import BytesIO

from flask import (
    Flask, jsonify, redirect, render_template, request, send_file,
    send_from_directory, session, url_for,
)
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import config
import sqlite3
from db import db, init_db
from gps_reader import get_gps_info
from odm_processor import dataset_dir
from pipeline import is_running, start_processing, start_tiles_only

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["UPLOAD_FOLDER"] = str(config.UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB per request

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
init_db()


# ---------------- AUTH HELPERS ----------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------- HOME ----------------

@app.route("/")
def home():
    return render_template("index.html")


# ---------------- LOGIN / SIGNUP / LOGOUT ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]

        with db() as conn:
            user = conn.execute(
                "SELECT id, username, password FROM users WHERE email=?", (email,)
            ).fetchone()

        if user and check_password_hash(user[2], password):
            session["user_id"] = user[0]
            session["username"] = user[1]
            return redirect(url_for("dashboard"))
        return "Invalid Email or Password"

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]

        try:
            with db() as conn:
                conn.execute(
                    "INSERT INTO users(username,email,password) VALUES(?,?,?)",
                    (username, email, generate_password_hash(password)),
                )
        except sqlite3.IntegrityError:
            return "Email already exists."

        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
@login_required
def dashboard():
    with db() as conn:
        stats = {
            "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "images": conn.execute("SELECT COUNT(*) FROM project_images").fetchone()[0],
            "measurements": conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0],
            "completed": conn.execute(
                "SELECT COUNT(*) FROM projects WHERE status='Completed'").fetchone()[0],
        }
        recent = conn.execute(
            "SELECT id, project_name, status FROM projects ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return render_template("dashboard.html", stats=stats, recent=recent)


# ---------------- PROJECTS ----------------

@app.route("/projects", methods=["GET", "POST"])
@login_required
def projects():
    with db() as conn:
        if request.method == "POST":
            conn.execute(
                "INSERT INTO projects(project_name,location,description) VALUES(?,?,?)",
                (request.form["project_name"], request.form["location"],
                 request.form["description"]),
            )
            conn.commit()

        rows = conn.execute("""
            SELECT id, project_name, location, created_at
            FROM projects ORDER BY id DESC
        """).fetchall()

    return render_template("projects.html", projects=rows)


@app.route("/delete_project/<int:id>")
@login_required
def delete_project(id):
    with db() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (id,))  # images/measurements cascade

    for folder in (
        config.UPLOAD_FOLDER / f"project_{id}",
        config.ORTHO_ROOT / f"project_{id}",
        dataset_dir(id),
    ):
        shutil.rmtree(folder, ignore_errors=True)

    return redirect(url_for("projects"))


@app.route("/project/<int:project_id>", methods=["GET", "POST"])
@login_required
def project_details(project_id):
    with db() as conn:
        project = conn.execute("""
            SELECT id, project_name, location, description,
                   created_at, status, progress
            FROM projects WHERE id=?
        """, (project_id,)).fetchone()

        if project is None:
            return "Project Not Found", 404

        project_folder = config.UPLOAD_FOLDER / f"project_{project_id}"

        if request.method == "POST":
            project_folder.mkdir(parents=True, exist_ok=True)

            for file in request.files.getlist("images"):
                if not file.filename:
                    continue
                filename = secure_filename(file.filename)
                file_path = project_folder / filename
                file.save(file_path)

                try:
                    print("GPS Data:", get_gps_info(file_path))
                except Exception as error:
                    print("GPS read failed:", error)

                exists = conn.execute(
                    "SELECT 1 FROM project_images WHERE project_id=? AND image_name=?",
                    (project_id, filename),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO project_images(project_id, image_name) VALUES (?, ?)",
                        (project_id, filename),
                    )
            conn.commit()

        images = conn.execute("""
            SELECT image_name FROM project_images
            WHERE project_id=? ORDER BY id DESC
        """, (project_id,)).fetchall()

    total_size = 0
    if project_folder.exists():
        total_size = sum(f.stat().st_size for f in project_folder.iterdir() if f.is_file())

    return render_template(
        "project.html",
        project=project,
        images=images,
        image_count=len(images),
        total_size=round(total_size / (1024 * 1024), 2),
        has_map=load_ortho_info(project_id) is not None,
    )


# ---------------- LEGACY UPLOAD PAGE ----------------

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        for file in request.files.getlist("images"):
            if file.filename:
                file.save(os.path.join(app.config["UPLOAD_FOLDER"],
                                       secure_filename(file.filename)))
        return redirect(url_for("upload"))

    images = [f for f in os.listdir(app.config["UPLOAD_FOLDER"])
              if os.path.isfile(os.path.join(app.config["UPLOAD_FOLDER"], f))]
    return render_template("upload.html", images=images)


@app.route("/delete/<filename>")
@login_required
def delete_image(filename):
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(filename))
    if os.path.isfile(file_path):
        os.remove(file_path)
    return redirect(url_for("upload"))


@app.route("/delete_image/<int:project_id>/<filename>")
@login_required
def delete_project_image(project_id, filename):
    filename = secure_filename(filename)

    with db() as conn:
        conn.execute(
            "DELETE FROM project_images WHERE project_id=? AND image_name=?",
            (project_id, filename),
        )

    image_path = config.UPLOAD_FOLDER / f"project_{project_id}" / filename
    if image_path.exists():
        image_path.unlink()

    return redirect(url_for("project_details", project_id=project_id))


# ---------------- GENERATE ORTHOMOSAIC ----------------

@app.route("/generate/<int:project_id>")
@login_required
def generate_orthomosaic(project_id):
    """ODM + tile generation background me chalta hai (request block nahi hoti)."""
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
    if not exists:
        return "Project Not Found", 404

    start_processing(project_id)
    return redirect(url_for("project_details", project_id=project_id))


@app.route("/status/<int:project_id>")
@login_required
def project_status(project_id):
    """Frontend isko poll kar sakta hai (har 5 sec) progress bar ke liye."""
    with db() as conn:
        row = conn.execute(
            "SELECT status, progress FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": row[0], "progress": row[1],
                    "running": is_running(project_id)})


# ---------------- ORTHOMOSAIC ----------------

def load_ortho_info(project_id):
    """tiles/bounds.json se bounds + tile url (rasterio ki zarurat nahi)."""
    info_path = config.ORTHO_ROOT / f"project_{project_id}" / "tiles" / "bounds.json"
    if not info_path.exists():
        return None
    info = json.loads(info_path.read_text())
    info["tiles_url"] = f"/static/orthomosaic/project_{project_id}/tiles/{{z}}/{{x}}/{{y}}.png"
    return info


@app.route("/orthomosaic/<int:project_id>")
@login_required
def orthomosaic(project_id):
    with db() as conn:
        project = conn.execute("""
            SELECT id, project_name, location, description, created_at
            FROM projects WHERE id=?
        """, (project_id,)).fetchone()
        image_count = conn.execute(
            "SELECT COUNT(*) FROM project_images WHERE project_id=?", (project_id,)
        ).fetchone()[0]

    if project is None:
        return "Project Not Found", 404

    return render_template("orthomosaic.html", project=project, image_count=image_count)


@app.route("/orthomosaic-file/<int:project_id>")
@login_required
def orthomosaic_file(project_id):
    folder = config.ORTHO_ROOT / f"project_{project_id}"
    if not (folder / "orthomosaic.tif").exists():
        return "Orthomosaic not generated yet", 404
    return send_from_directory(folder, "orthomosaic.tif", as_attachment=True)


# ---------------- MAP VIEWER ----------------

@app.route("/map-viewer")
@app.route("/map-viewer/<int:project_id>")
@login_required
def map_viewer(project_id=None):
    with db() as conn:
        all_projects = conn.execute("""
            SELECT id, project_name, location FROM projects ORDER BY id DESC
        """).fetchall()

        selected_project = None
        if project_id is not None:
            selected_project = conn.execute("""
                SELECT id, project_name, location FROM projects WHERE id=?
            """, (project_id,)).fetchone()

    if project_id is not None and selected_project is None:
        return "Project Not Found", 404

    info = load_ortho_info(project_id) if selected_project else None

    return render_template(
        "map_viewer.html",
        projects=all_projects,
        selected_project=selected_project,
        orthomosaic_bounds=info["bounds"] if info else None,
        tiles_url=info["tiles_url"] if info else None,
        min_zoom=info["min_zoom"] if info else None,
        max_zoom=info["max_zoom"] if info else None,
    )


@app.route("/3d-viewer")
@login_required
def viewer_3d():
    return render_template("3d_viewer.html")


# ---------------- UPLOAD READY ORTHOMOSAIC (no Docker needed) ----------------

@app.route("/upload-ortho/<int:project_id>", methods=["POST"])
@login_required
def upload_ortho(project_id):
    """WebODM / Metashape / Pix4D se bani GeoTIFF upload karo -> tiles apne aap bante hain."""
    file = request.files.get("ortho")
    if not file or not file.filename.lower().endswith((".tif", ".tiff")):
        return "Please choose a .tif / .tiff GeoTIFF file.", 400
    if is_running(project_id):
        return "Is project par processing already chal rahi hai.", 409

    with db() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            return "Project Not Found", 404

    folder = config.ORTHO_ROOT / f"project_{project_id}"
    folder.mkdir(parents=True, exist_ok=True)
    file.save(folder / "orthomosaic.tif")
    start_tiles_only(project_id)
    return redirect(url_for("project_details", project_id=project_id))


@app.route("/api/measurements/<int:project_id>")
@login_required
def api_measurements(project_id):
    with db() as conn:
        rows = conn.execute("""
            SELECT id, plot_name, area_sqm, coordinates
            FROM measurements WHERE project_id=? ORDER BY id
        """, (project_id,)).fetchall()
    return jsonify([
        {"id": r[0], "plot_name": r[1], "area_sqm": r[2], "coordinates": json.loads(r[3] or "[]")}
        for r in rows
    ])


# ---------------- MEASUREMENTS ----------------

@app.route("/measurements/<int:project_id>")
@login_required
def view_measurements(project_id):
    with db() as conn:
        rows = conn.execute("""
            SELECT id, plot_name, area_sqm, area_hectares, area_acres, created_at
            FROM measurements WHERE project_id=? ORDER BY id DESC
        """, (project_id,)).fetchall()

    return render_template("measurements.html", project_id=project_id, measurements=rows)


@app.route("/save-measurement/<int:project_id>", methods=["POST"])
@login_required
def save_measurement(project_id):
    data = request.get_json(silent=True) or {}
    plot_name = str(data.get("plot_name", "")).strip()
    coordinates = data.get("coordinates")
    area_sqm = data.get("area_sqm")

    if not plot_name or not coordinates or area_sqm is None:
        return jsonify({"success": False,
                        "message": "Plot name, coordinates and area are required."}), 400

    try:
        area_sqm = float(area_sqm)
        if area_sqm <= 0:
            raise ValueError("Area must be positive")
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid area value."}), 400

    with db() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            return jsonify({"success": False, "message": "Project not found."}), 404

        conn.execute("""
            INSERT INTO measurements
            (project_id, plot_name, area_sqm, area_hectares, area_acres, coordinates)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (project_id, plot_name, area_sqm, area_sqm / 10000,
              area_sqm / 4046.8564224, json.dumps(coordinates)))

    return jsonify({"success": True, "message": "Measurement saved successfully."})


@app.route("/delete-measurement/<int:project_id>/<int:measurement_id>")
@login_required
def delete_measurement(project_id, measurement_id):
    with db() as conn:
        conn.execute("DELETE FROM measurements WHERE id=? AND project_id=?",
                     (measurement_id, project_id))
    return redirect(url_for("view_measurements", project_id=project_id))


def _fmt(value, digits):
    return "" if value is None else f"{value:,.{digits}f}"


def _fmt(value, digits):
    return "" if value is None else f"{value:,.{digits}f}"


def _fetch_measurements(project_id):
    with db() as conn:
        return conn.execute("""
            SELECT plot_name, area_sqm, area_hectares, area_acres, created_at
            FROM measurements WHERE project_id=? ORDER BY id DESC
        """, (project_id,)).fetchall()


@app.route("/export-measurements/<int:project_id>/pdf")
@login_required
def export_measurements_pdf(project_id):
    measurements = _fetch_measurements(project_id)

    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4),
                                 title="AeroMap AI - Measurement Report",
                                 author="AeroMap AI")
    styles = getSampleStyleSheet()
    elements = [Paragraph("AeroMap AI - Measurement Report", styles["Title"]), Spacer(1, 20)]

    data = [["Plot Name", "Area (sqm)", "Hectares", "Acres", "Created At"]]
    for m in measurements:
        data.append([m[0], _fmt(m[1], 2), _fmt(m[2], 4), _fmt(m[3], 4), str(m[4])])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.darkgreen),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    document.build(elements)
    output.seek(0)

    return send_file(output, as_attachment=True,
                     download_name=f"project_{project_id}_measurements.pdf",
                     mimetype="application/pdf")


@app.route("/export-measurements/<int:project_id>/excel")
@login_required
def export_measurements_excel(project_id):
    wb = Workbook()
    ws = wb.active
    ws.title = "Measurements"
    ws.append(["Plot Name", "Area (sqm)", "Hectares", "Acres", "Created At"])
    for m in _fetch_measurements(project_id):
        ws.append([m[0], round(m[1] or 0, 2), round(m[2] or 0, 4), round(m[3] or 0, 4), m[4]])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output, as_attachment=True,
        download_name=f"project_{project_id}_measurements.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------- MAIN ----------------

if __name__ == "__main__":
    # use_reloader=False: warna code change pe background ODM thread mar jata hai
    app.run(debug=True, use_reloader=False)