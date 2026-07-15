import os
import sqlite3
import uuid
import boto3
from flask import Flask, request, jsonify, render_template, g, redirect, url_for, flash
from werkzeug.utils import secure_filename
from datetime import datetime
from dotenv import load_dotenv
import json

from flask_turnstile import Turnstile
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024

auth = HTTPBasicAuth()
ADMIN_PASSWORD = generate_password_hash(os.getenv('ADMIN_PASSWORD'))

app.config['TURNSTILE_SITE_KEY'] = os.getenv("TURNSTILE_SITE_KEY")
app.config['TURNSTILE_SECRET_KEY']  = os.getenv("TURNSTILE_SECRET_KEY")
turnstile = Turnstile(app=app)

DATABASE = 'data/db.sqlite3'

countries = json.load(open('static/countries.json'))
countries.pop("NP")

R2_BUCKET = os.getenv("R2_BUCKET")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

s3 = boto3.client(
    's3',
    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name='auto'
)


@auth.verify_password
def verify_password(username, password):
    if username == "admin" and \
            check_password_hash(ADMIN_PASSWORD, password):
        return username


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                job_role TEXT,
                experience TEXT,
                job_types TEXT,
                sources TEXT,
                cv_filename TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        db.execute('''
            CREATE TABLE IF NOT EXISTS submission_cities (
                submission INTEGER,
                city INTEGER,
                FOREIGN KEY (submission) REFERENCES submissions (id),
                FOREIGN KEY (city) REFERENCES city (id)
            )
        ''')

        db.commit()

init_db()


@app.get('/') 
def home():
    return render_template('home.html',countries=countries)


ALLOWED_EXPERIENCES = {'entry', 'junior', 'mid', 'senior'}
ALLOWED_JOB_TYPES = {'remote', 'onsite'}

@app.post('/')
def submit():
    try:
        email = request.form.get('email')
        job_role = request.form.get('job_role')
        experience = request.form.get('experience')

        if not email or not job_role or not experience:
            return render_template('home.html', countries=countries, message="Incomplete form submission. Please resubmit the form will all values filled.")

        if experience not in ALLOWED_EXPERIENCES:
            return render_template('home.html', countries=countries, message="Invalid working experience level provided.")

        if not turnstile.verify():
            return render_template('home.html', countries=countries, message="Captcha validation failed. Please try again.")

        job_types = request.form.getlist('job_type')
        if not job_types or not set(job_types).issubset(ALLOWED_JOB_TYPES):
            return render_template('home.html', countries=countries, message="Missing job type: Must choose remote, or onsite, or both.")


        sources = ', ' + ', '.join(request.form.getlist('sources'))

        selected_city_ids = None
        if 'onsite' in job_types:
            selected_city_ids = request.form.getlist('selected_locations[]')
            if not selected_city_ids:
                return render_template('home.html', countries=countries, message="Invalid job type: You selected the Onsite option but did not choose any location.")

        job_types = ', '.join(job_types)

        cv_filename = None
        if 'cv' in request.files:
            file = request.files['cv']
            if file.filename != '':
                if not file.filename.lower().endswith('.pdf'):
                    return render_template('home.html', countries=countries, message="Only PDF file is allowed for the CV.")

                magic_bytes = file.read(4)
                if magic_bytes != b'%PDF':
                    return render_template('home.html', countries=countries, message="Corrupted PDF file detected.")

                file.seek(0, 2)  # Jump to end
                file_length = file.tell() # Get byte position
                if file_length > (5 * 1024 * 1024): # 5MB limit
                    return render_template('home.html', countries=countries, message="CV file size must be less than 5MB.")
                file.seek(0)  # CRITICAL: Reset the file pointer back to the beginning before uploading to S3

                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
                
                s3.upload_fileobj(
                    file, 
                    R2_BUCKET, 
                    unique_filename,
                    ExtraArgs={'ContentType': 'application/pdf'}
                )
                cv_filename = unique_filename

        db = get_db()
        cursor = db.execute('''
            INSERT INTO submissions 
            (email, job_role, experience, job_types, sources, cv_filename)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (email, job_role, experience, job_types, sources, cv_filename))
        db.commit()

        submission_id = cursor.lastrowid
        if selected_city_ids:
            db_locations = [(submission_id, cid) for cid in selected_city_ids]
            db.executemany("INSERT INTO submission_cities (submission, city) VALUES (?, ?)", db_locations)
        db.commit()

        return render_template('home.html', countries=countries, message="You will now start receiving matching vacancies in your mailbox. Only trust mails from jobs@prax.to")

    except Exception as e:
        print(f"Error: {e}")
        return render_template('home.html', countries=countries, message="Ugh! Something went wrong. Please try again later.")


@app.get('/admin')
@auth.login_required
def admin():
    db = get_db()
    
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    
    role_filter = request.args.get('role', '')

    query = """
        SELECT s.*, 
               GROUP_CONCAT(c.name || ', ' || co.name, ' | ') as formatted_locations
        FROM submissions s
        LEFT JOIN submission_cities sc ON s.id = sc.submission
        LEFT JOIN city c ON sc.city = c.id
        LEFT JOIN country co ON c.country_code = co.code
    """
    
    count_query = "SELECT COUNT(*) FROM submissions"
    params = []

    if role_filter:
        query += " WHERE s.job_role LIKE ?"
        count_query += " WHERE job_role LIKE ?"
        params.append(f"%{role_filter}%")

    query += " GROUP BY s.id ORDER BY s.created_at DESC LIMIT ? OFFSET ?"
    
    total_records = db.execute(count_query, params).fetchone()[0]
    
    submissions = db.execute(query, params + [per_page, offset]).fetchall()
    
    total_pages = (total_records + per_page - 1) // per_page

    return render_template('admin.html', 
                           submissions=submissions, 
                           page=page, 
                           total_pages=total_pages,
                           role_filter=role_filter, R2_PUBLIC_URL=R2_PUBLIC_URL)

@app.post('/admin')
@auth.login_required
def admin_del_submission():
    submission_id = request.form.get('sid')
    
    if not submission_id:
        flash("Invalid submission ID.", "error")
        return redirect(url_for('admin'))

    db = get_db()

    row = db.execute(
        'SELECT cv_filename FROM submissions WHERE id = ?', 
        (submission_id,)
    ).fetchone()

    if not row:
        flash("Submission not found.", "error")
        return redirect(url_for('admin'))

    cv_filename = row['cv_filename']

    if cv_filename:
        try:
            s3.delete_object(Bucket=R2_BUCKET, Key=cv_filename)
        except Exception as e:
            print(f"Failed to delete CV {cv_filename} from R2: {e}")

    try:
        db.execute('DELETE FROM submission_cities WHERE submission = ?', (submission_id,))
        db.execute('DELETE FROM submissions WHERE id = ?', (submission_id,))
        db.commit()
        flash("Submission and associated files deleted successfully!", "success")
    except Exception as e:
        db.rollback()
        print(f"Database deletion error: {e}")
        flash("Failed to delete submission from database.", "error")

    return redirect(url_for('admin'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)
