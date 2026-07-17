import sqlite3
from collections import defaultdict

DATABASE = 'data/db.sqlite3'

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
            sent_at TEXT NOT NULL,
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
                    INSERT OR IGNORE INTO email_queue (email, roles, vacancy_id, sent_at)
                    VALUES (?, ?, ?, NULL)
                ''', db_queue)
                db.commit()
        
if __name__ == "__main__":
    sort_vacancies_per_email()
    # send_mails()
