import requests
import json
import sqlite3

DATABASE = 'db.sqlite3'
BASE_URL = "https://raw.githubusercontent.com/dr5hn/countries-states-cities-database/refs/heads/master/contributions/"

def init_db(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS country (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS city (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT,
            name TEXT NOT NULL,
            FOREIGN KEY (country_code) REFERENCES country (code),
            UNIQUE (country_code, name)
        );
    ''')


def export_cities_to_json(countries_dict):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    for country_code, country_name in countries_dict.items():
        rows = cursor.execute("""
            SELECT id, name FROM city 
            WHERE country_code = ? 
            ORDER BY (name = 'All cities') DESC, name ASC
        """, (country_code,)).fetchall()

        cities_list = []
        
        for row in rows:
            cities_list.append({
                "code": str(row['id']),
                "name": row['name']
            })

        file_path = f"static/cities/{country_code}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(cities_list, f, indent=2, ensure_ascii=False)

    conn.close()

def fetch_and_save_data():
    print("Fetching countries list...")
    response = requests.get(f"{BASE_URL}/countries/countries.json")
    response.raise_for_status()
    countries_list = response.json()
    
    tot = len(countries_list)

    all_countries={}

    db_countries = []
    db_cities = []

    for i, country in enumerate(countries_list, 1):
        country_code = country["iso2"].upper()
        country_name = country["name"]

        all_countries[country_code] = country["name"]

        db_countries.append((country_code, country_name))
        db_cities.append((country_code, "All cities"))
            
        print(f"{i}/{tot}. {country['name']} ({country_code})...")
        city_req = requests.get(f"{BASE_URL}/cities/{country_code}.json")

        if city_req.status_code == 200:
            for city in city_req.json():
                city_code = str(city.get("state_code", ""))
                city_name = city["name"]

                db_cities.append((country_code, city_name))
                
    with sqlite3.connect(DATABASE) as conn:
        init_db(conn)
        conn.executemany("INSERT OR REPLACE INTO country (code, name) VALUES (?, ?)", db_countries)
        conn.executemany("INSERT OR IGNORE INTO city (country_code, name) VALUES (?, ?)", db_cities)
        conn.commit()

    with open("static/countries.json", "w", encoding="utf-8") as f:
        json.dump(all_countries, f, indent=2, ensure_ascii=False)

    export_cities_to_json(all_countries)

    print("Done!")

if __name__ == "__main__":
    fetch_and_save_data()
