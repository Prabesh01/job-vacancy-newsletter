import sqlite3
import os
from collections import defaultdict
import base64
import jwt
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from dotenv import load_dotenv
load_dotenv()
secret_key = os.getenv("FLASK_SECRET_KEY")

WEB_URL = os.getenv("WEB_URL")
unsubscribe_link = f"{WEB_URL}/unsubscribe?token="

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_FROM = os.environ.get("SMTP_FROM")
MAIL_SUBJECT = "Job Vacancy Newsletter"

DATABASE = 'data/db.sqlite3'
FOOTER_FILE = 'data/footer'

if not os.path.exists(FOOTER_FILE): FOOTER = None
else: 
    with open(FOOTER_FILE,'r') as f: FOOTER = f.read()

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    db = get_db()

    db.execute('''
        CREATE TABLE IF NOT EXISTS email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            roles TEXT NOT NULL,
            vacancy_id INTEGER NOT NULL,
            sent_at TEXT,
            UNIQUE(email, vacancy_id),
            FOREIGN KEY(vacancy_id) REFERENCES vacancies(id)
        );
    ''')
    db.commit()

init_db()


def get_eligible_users_for_vacancy(db, vacancy):
    vac_roles = [r.strip() for r in vacancy['role'].split(',')]
    if not vac_roles:
        return []

    role_placeholders = ', '.join('?' for _ in vac_roles)    

    experience_clause = ""
    experience_params = []
    if vacancy['experience']:
        experience_clause = "AND s.experience = ?"
        experience_params = [vacancy['experience']]

    if vacancy['is_remote'] == 1:
        query = f"""
            SELECT DISTINCT s.* 
            FROM submissions s
            WHERE s.job_types LIKE '%remote%'
              AND s.job_role IN ({role_placeholders})
              {experience_clause}
        """
        params = vac_roles + experience_params
        return db.execute(query, params).fetchall()

    else:
        query = f"""
            SELECT DISTINCT s.* 
            FROM submissions s
            JOIN submission_cities sc ON s.id = sc.submission
            WHERE s.job_types LIKE '%onsite%'
              AND s.job_role IN ({role_placeholders})
              AND sc.city IN (
                  ?,
                  (SELECT id FROM city 
                   WHERE country_code = (SELECT country_code FROM city WHERE id = ?) 
                     AND name = 'All cities')
              )
              {experience_clause}
        """
        params = vac_roles + [vacancy['city'], vacancy['city']] + experience_params
        return db.execute(query, params).fetchall()    


def sort_vacancies_per_email():
    with get_db() as db:
        pending_vacancies = db.execute("SELECT * FROM vacancies WHERE emailed = 0 AND processed = 1").fetchall()

        for vacancy in pending_vacancies:
            if not vacancy['role']:
                continue

            eligible_users = get_eligible_users_for_vacancy(db, vacancy)

            email_role_map = defaultdict(set)
            for user in eligible_users:
                email_role_map[user['email']].add(user['job_role'])

            db_queue = []
            for email, roles in email_role_map.items():
                matched_roles_str = " / ".join(sorted(roles))
                db_queue.append((email, matched_roles_str, vacancy['id']))

            if db_queue:
                db.executemany('''
                    INSERT OR IGNORE INTO email_queue (email, roles, vacancy_id)
                    VALUES (?, ?, ?)
                ''', db_queue)
                db.commit()


def get_pending_queue_by_email(db):
    rows = db.execute("""
        SELECT eq.id AS queue_id, eq.email, eq.roles, eq.vacancy_id,
               v.title, v.company, v.url, v.is_remote,
               CASE 
                    WHEN c.name = 'All cities' THEN co.name
                    ELSE (c.name || ', ' || co.name)
                END AS formatted_location
        FROM email_queue eq
        JOIN vacancies v ON v.id = eq.vacancy_id
        LEFT JOIN city c ON v.city = c.id
        LEFT JOIN country co ON c.country_code = co.code
        WHERE eq.sent_at IS NULL
        ORDER BY eq.email
    """).fetchall()
 
    by_email = defaultdict(list)
    for row in rows:
        by_email[row['email']].append(dict(row))
    return by_email


def generate_unsubscribe_section(db, email):
    # get user's subsriptions
    query = f"""
        SELECT id, job_role
        FROM submissions where email = ? 
    """
    subscriptions = db.execute(query, [email]).fetchall()

    topics = {}
    for sub in subscriptions:
        payload = {
            'email': email,
            'id': sub['id']
        }
        token = jwt.encode(payload, secret_key, algorithm='HS256')
        topics[sub['job_role']] = token

    all_token = jwt.encode({'email': email, 'id': 'all'}, secret_key, algorithm='HS256')

    txt=f"""You can unsubscribe anytime from <a href="{unsubscribe_link}{all_token}">all</a> or individual roles: """

    links = []
    for role, token in topics.items():
        links.append(f'<a href="{unsubscribe_link}{token}">{role}</a>')

    txt += ", ".join(links) + "."
    return txt


def render_email_body(queue_rows):
    groups = defaultdict(list)
    for row in queue_rows:
        groups[row['roles']].append(row)
 
    sections = sorted(groups.items(), key=lambda kv: (-kv[0].count('/'), kv[0].lower()))
 
    lines = ["Here's what we found for you:<br>"]
    for label, rows in sections:
        lines.append(f"<b>{label}:</b>")
        for row in rows:
            location = "Remote" if row['is_remote'] else (row['formatted_location'] or "Onsite")
            company = f" @ {row['company']}" if row['company'] else ""
            lines.append(f"""- <a href="{row['url']}">{row['title']}{company} ({location})</a>""")
        lines.append("")
 
    if FOOTER:
        lines.append(f"<i>{FOOTER}</i>")
        lines.append("")

    return "<br>".join(lines).strip()


def send_email(to_email, body):
    msg = MIMEText(body, 'html')
    msg['Subject'] = MAIL_SUBJECT
    msg['From'] = SMTP_FROM
    msg['To'] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_FROM, [to_email], msg.as_string())


def send_newsletter_mails():
    db = get_db()

    pending = get_pending_queue_by_email(db)
    if not pending:
        # current unmailed vacancies do not fir for any of the subscriber. 
        # close them so it wont trigger after a long time in future when it matches a user after deadline
        db.execute("Update vacancies SET emailed=2 where emailed=0 and processed=1")
        db.commit()
        print("No pending emails to send.")
        return
    print(f"{sum(len(r) for r in pending.values())} pending vacancy links across {len(pending)} recipients.")

    failed_vacancy_ids = set()
    attempted_vacancy_ids = set()
 
    for email, rows in pending.items():
        attempted_vacancy_ids |= {r['vacancy_id'] for r in rows}
        body = render_email_body(rows) + "<br><br>---<br><br>" + generate_unsubscribe_section(db, email)
 
        try:
            send_email(email, body)
        except Exception as exc:
            print(f"Failed to send to {email}: {exc}")
            failed_vacancy_ids |= {r['vacancy_id'] for r in rows}
            continue
 
        sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.executemany(
            "UPDATE email_queue SET sent_at = ? WHERE id = ?",
            [(sent_at, row['queue_id']) for row in rows]
        )
        #db.commit()
        print(f"Sent to {email}: {len(rows)} vacancy link(s).")

    settled_ids = attempted_vacancy_ids - failed_vacancy_ids
    if settled_ids:
        db.executemany(
            "UPDATE vacancies SET emailed = 1 WHERE id = ?",
            [(vid,) for vid in settled_ids]
        )
        db.commit()
    print(f"Marked {len(settled_ids)} vacancies as emailed; "
          f"{len(failed_vacancy_ids)} are to be retried later.")


if __name__ == "__main__":
    sort_vacancies_per_email()
    send_newsletter_mails()
