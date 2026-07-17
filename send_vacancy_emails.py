import sqlite3

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

    role_placeholders = ', '.join('?' for _ in vac_roles)    

    if vacancy['is_remote'] == 1:
        query = f"""
            SELECT DISTINCT s.* 
            FROM submissions s
            WHERE s.experience = ?
              AND s.job_types LIKE '%remote%'
              AND s.job_role IN ({role_placeholders})
        """
        params = [vacancy['experience']] + vac_roles
        return db.execute(query, params).fetchall()

    else:
        query = f"""
            SELECT DISTINCT s.* 
            FROM submissions s
            JOIN submission_cities sc ON s.id = sc.submission
            WHERE s.experience = ?
              AND s.job_types LIKE '%onsite%'
              AND s.job_role IN ({role_placeholders})
              AND sc.city IN (
                  ?,
                  (SELECT id FROM city 
                   WHERE country_code = (SELECT country_code FROM city WHERE id = ?) 
                     AND name = 'All cities')
              )
        """
        params = [vacancy['experience']] + vac_roles + [vacancy['city'], vacancy['city']]
        return db.execute(query, params).fetchall()    


def sort_vacancies_per_email():
    with get_db() as db:
        unprocessed = db.execute("SELECT * FROM vacancies WHERE emailed = 0").fetchall()

        for vacancy in unprocessed:
            eligible_users = get_eligible_users_for_vacancy(db, vacancy)

            email_role_map = {}
            for user in eligible_users:
                email = user['email']
                role = user['job_role']
            
                if email not in email_role_map:
                    email_role_map[email] = set()
                email_role_map[email].add(role)

            db_queue = []
            for email, roles in email_role_map.items():
                matched_roles_str = " / ".join(sorted(roles))
                db_queue.append((email, matched_roles_str, vacancy['id']))

            if db_queue:
                db.executemany('''
                    INSERT OR IGNORE INTO email_queue (email, roles, vacancy_id)
                    VALUES (?, ?, ?)
                ''', db_queue)
        
if __name__ == "__main__":
    sort_vacancies_per_email()
    # send_mails()
