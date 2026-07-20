import sqlite3

DATABASE = 'data/db.sqlite3'


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


db = get_db()

query = "select distinct ci.country_code from submissions s join submission_cities sc on sc.submission=s.id join city ci on sc.city=ci.id"
required_countries = db.execute(query).fetchall()
print(len(required_countries))
rc_list=[]
for rc in required_countries: rc_list.append(rc['country_code'])
print(rc_list)

print('----')

query = "select distinct ci.country_code from vacancies v join city ci on v.city=ci.id where is_remote=0 and processed=1 and emailed=0 and source='linkedin'"
vacancies_countries = db.execute(query).fetchall()
print(len(vacancies_countries))
vc_list=[]
for vc in vacancies_countries: vc_list.append(vc['country_code'])
print(vc_list)

print('----')

placeholders = ','.join(['?'] * len(rc_list))
query = f"select count(v.id) from vacancies v join city ci on v.city=ci.id where is_remote=0 and processed=1 and emailed=0 and source='linkedin' and ci.country_code not in ({placeholders})"
count = db.execute(query, rc_list).fetchone()[0]
print(count)

query = f"delete from vacancies where id in (select v.id from vacancies v join city ci on v.city=ci.id where is_remote=0 and processed=1 and emailed=0 and source='linkedin' and ci.country_code not in ({placeholders}))"
count = db.execute(query, rc_list)
db.commit()
