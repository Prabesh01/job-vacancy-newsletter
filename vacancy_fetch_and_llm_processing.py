import os
from dotenv import load_dotenv
load_dotenv()

import sqlite3
import requests
import json
import asyncio
from datetime import datetime, date, timezone
from typing import List, Optional, Literal

DATABASE = 'data/db.sqlite3'

from pydantic import BaseModel, ValidationError
from openai import AsyncOpenAI
import itertools
MAX_CONCURRENCY = 3

from bs4 import BeautifulSoup

PROVIDERS = [
    {
        "name": "Gemini 3.1 Flash-Lite",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": os.getenv("GEMINI_API_KEY"),
        "model": "gemini-3.1-flash-lite"
    },
    {
        "name": "Groq-Llama3",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": "llama-3.3-70b-versatile"
    },
    {
        "name": "Groq-gpt-oss-120b",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": os.getenv("GROQ_API_KEY2"),
        "model": "openai/gpt-oss-120b"
    },
    {
        "name": "Mistral Large 3",
        "base_url": "https://api.mistral.ai/v1",
        "api_key": os.getenv("MISTRAL_API_KEY"),
        "model": "mistral-large-2512"
    }
]

EXPERIENCE_LEVELS = ['entry', 'junior', 'mid', 'senior']

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


class LLMRotator:
    def __init__(self, providers):
        self.providers = providers

        self.clients = {i: self._create_client(i) for i in range(len(providers))}
        self.cycle = itertools.cycle(range(len(providers)))
        self.lock = asyncio.Lock()

    def _create_client(self, idx):
        p = self.providers[idx]
        return AsyncOpenAI(api_key=p['api_key'], base_url=p['base_url'])

    async def next(self):
        async with self.lock:
            idx = next(self.cycle)
        return self.clients[idx], self.providers[idx]

rotator = LLMRotator(PROVIDERS)


def spirejob(db):
   print("> Spirejob")
   fallback_city_row = db.execute(
        'SELECT id FROM city WHERE country_code = "NP" AND LOWER(name) = "all cities"'
   ).fetchone()
   fallback_city_id = fallback_city_row['id'] if fallback_city_row else None
 
   for i in range(0,1000,100):
       print(f"     - Page: {(i/100)+1}")
       rows=[]

       jobs=requests.get(f"https://spirejob.com/api/search/results?limit=100&offset={i}").json()['jobs']

       if not jobs: break

       fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
       for job in jobs:
           if not job['status']=="open": continue
           deadline = job['expires_at']
           if deadline:
               dt = datetime.fromisoformat(deadline)
               if dt.date() <= date.today():
                   continue

           jid = str(job['id'])
           title = job['title']
           district = job.get('district','')
           company = job['company_name']
           url = job['external_apply_url']
           
           is_remote = 1 if job['workplace_type'].lower() == 'remote' else 0
           is_remote = 1 if job['district'].lower() == 'remote' else is_remote
           city_id=None

           if is_remote == 0 and district:
               city_query = 'SELECT id FROM city WHERE country_code = "NP" and LOWER(name) = ?'
               city_row = db.execute(city_query, (district.lower(),)).fetchone()
               city_id = city_row['id'] if city_row else fallback_city_id

           description = f"Title: {title}\n"
           description += requests.get(f"https://spirejob.com/api/jobs/{jid}").json()['description']
           rows.append((jid,title,is_remote,city_id,company,url,description,fetched_at))

       db.executemany("INSERT OR IGNORE INTO vacancies (source,external_id,title,is_remote,city,company,url,description,processed,emailed,fetched_at) VALUES ('spirejob', ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)", rows)
       db.commit()


def parse_linkedin_job_list(db, content, fetched_at, is_remote=0, country_code=None, city_id=None):
    soup = BeautifulSoup(content, "html.parser")
    jobs = soup.find_all('div',class_="base-card")
    jid = 0
    rows = []
    for job in jobs:
        title = job.find(class_="base-search-card__title").text.strip()
        company = job.find(class_="base-search-card__subtitle").text.strip()

        url = job.find('a')['href']
        jid = url.split('?')[0].split('-')[-1]

        exist= db.execute(
            'SELECT id FROM vacancies WHERE source="linkedin" and external_id = ?',
            (jid,)
        ).fetchone()
        if exist: continue

        url = f"https://www.linkedin.com/jobs/view/{jid}"

        address = job.find(class_="job-search-card__location").text.strip().split(',')
        country = address[-1]
        city = address[0]

        if country_code: city_id = resolve_city_id(db, country_code, city)

        rr = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
        soup2 = BeautifulSoup(rr.content, "html.parser")
        description = soup2.find("section",class_="description").text.strip()

        rows.append((jid, title, is_remote, city_id, company, url, description, fetched_at))

    db.executemany("INSERT OR IGNORE INTO vacancies (source,external_id,title,is_remote,city,company,url,description,processed,emailed,fetched_at) VALUES ('linkedin', ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)", rows)
    db.commit()
    return jid, len(jobs)


def linkedin_remote(db, job_role):
    start = 0
    last_jid = 0
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i in range(10):
        r=requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={job_role}&location=worldwide&f_WT=2&start={start}")
        jid, total_jobs = parse_linkedin_job_list(db, r.content, fetched_at, is_remote=1)
        if last_jid == jid: break
        last_jid = jid
        start = total_jobs+1


def linkedin_country(db, country_code, country_name, job_role):
    start = 0
    last_jid = 0
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i in range(10):
        r=requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={job_role}&location={country_name}&f_WT=1&start={start}")
        jid, total_jobs = parse_linkedin_job_list(db, r.content, fetched_at, is_remote=0, country_code=country_code)
        if last_jid == jid: break
        last_jid = jid
        start = total_jobs+1


def linkedin_city(db, city_id, city_name, country_name, job_role):
    matching_cities = requests.get(f"https://www.linkedin.com/jobs-guest/api/typeaheadHits?origin=jserp&typeaheadType=GEO&geoTypes=POPULATED_PLACE&query={city_name.lower()}").json()
    if not matching_cities: return

    linkedin_geo_id=0
    for city in matching_cities:
        if country_name.lower() in city['displayName'].lower():
            linkedin_geo_id = city['id']
            break

    if linkedin_geo_id==0: return

    start = 0
    last_jid = 0
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i in range(10):
        r=requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?geoId={linkedin_geo_id}&keywords={job_role}&f_WT=1&start={start}")
        jid, total_jobs = parse_linkedin_job_list(db, r.content, fetched_at, is_remote=0, city_id=city_id)
        if last_jid == jid: break
        last_jid = jid
        start = total_jobs+1


def linkedin(db):
    print("> linkedin")
    # keywords = ["developer","flutter","mobile","website","javascript","python","database","administrator","customer","fullstack","devops","devsecops","cybersecurity","backend","laravel","php","software","human resource","marketting","java",".net","sales"]
    query = """
        SELECT DISTINCT s.job_role, s.job_types,
               c.name as city_name, c.id as city_id, co.name as country_name, co.code as country_code
        FROM submissions s
        LEFT JOIN submission_cities sc ON s.id = sc.submission
        LEFT JOIN city c ON sc.city = c.id
        LEFT JOIN country co ON c.country_code = co.code
    """
    submissions = db.execute(query).fetchall()
    total_subs = len(submissions)
    i=0
    for sub in submissions:
        i+=1
        print(f"     - {i}/{total_subs}")

        job_role = sub['job_role']
        if 'remote' in sub['job_types']:
            linkedin_remote(db, job_role)
        if 'onsite' in sub['job_types']:
            if sub['city_name'].lower() == "all cities": linkedin_country(db, sub['country_code'], sub['country_name'], job_role)
            else: linkedin_city(db, sub['city_id'], sub['city_name'], sub['country_name'], job_role)


class JobExtraction(BaseModel):
    title: str
    company_name: str
    mapped_roles: List[str]
    experience_level: str
    is_remote: bool
    country_code: Optional[str] = None
    city_name: Optional[str] = None


def get_allowed_roles(db):
    rows = db.execute(
        "SELECT DISTINCT job_role FROM submissions WHERE job_role IS NOT NULL AND job_role != ''"
    ).fetchall()
    return sorted({r['job_role'] for r in rows})


def build_prompt(job, allowed_roles):
    return f"""Extract job posting details as a JSON object with EXACTLY these keys:
- title: concise job title (string)
- company_name: company name if present in the job posting details. else leave empty 
- mapped_roles: array of strings, zero or more from this exact list: {allowed_roles}. Empty array if none apply - never invent roles outside this list.
- experience_level: exactly one of {EXPERIENCE_LEVELS}. YoE of entry=0-1yr, junior=1-3yr, mid=3-5yr, senior=5+yr. Infer from years mentioned or seniority language (Senior/Lead/Junior/etc).
- is_remote: boolean. true if remote job, false for hybrid/onside
- country_code: 2-letter ISO country code if onsite/hybrid, else null
- city_name: city name if onsite/hybrid, else null
 
Job posting:
{job['description'][:2500]}
 
Respond with ONLY the JSON object, no other text."""


async def call_llm(prompt, client, MODEL, retry_note=None):
    messages = [
        {"role": "system", "content": "You are an expert HR data extractor. Output only valid JSON matching the requested schema."},
        {"role": "user", "content": prompt if not retry_note else f"{prompt}\n\n{retry_note}"},
    ]
    resp = await client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return resp.choices[0].message.content


async def extract_job(job, allowed_roles, semaphore):
    prompt = build_prompt(job, allowed_roles)
    last_exc = None
    retry_note = None
    async with semaphore:
        client, config = await rotator.next()
        for attempt in range(4):
            try:
                raw = await call_llm(prompt, client, config['model'], retry_note)
                return JobExtraction.model_validate(json.loads(raw))
            except Exception as exc:
                err_msg = str(exc)

                if "429" in err_msg or "rate_limit" in err_msg:
                    if attempt < 3:
                        wait_time = (attempt + 1) * 6
                        print(f"  [Rate Limit Hit] Pausing for {wait_time}s before retrying Vacancy {job['id']}...")
                        await asyncio.sleep(wait_time)
                    else:
                        print(f"  [!] {config['name']} rate-limited on Vacancy {job['id']}.  Rotating...") 
                        client, config = await rotator.next()
                    retry_note = None
                    continue
                    
                elif isinstance(exc, (json.JSONDecodeError, ValidationError)):
                    last_exc = exc
                    retry_note = f"Your previous response was invalid ({exc}). Return ONLY valid JSON matching the schema."
                    continue
                    
                else:
                    print(f"  Vacancy {job['id']}: Unknown LLM error: {exc}")
                    return None

    print(f"  Vacancy {job['id']}: invalid LLM output multiple times, skipping ({last_exc})")
    return None


def resolve_city_id(db, country_code, city_name):
    if not country_code:
        return None
    if city_name:
        row = db.execute(
            "SELECT id FROM city WHERE country_code = ? AND LOWER(name) = ?",
            (country_code, city_name.strip().lower())
        ).fetchone()
        if row:
            return row['id']
    row = db.execute(
        "SELECT id FROM city WHERE country_code = ? AND LOWER(name) = 'all cities'",
        (country_code,)
    ).fetchone()
    return row['id'] if row else None


def apply_extraction(db, job, parsed, allowed_roles):
    allowed_lower = {r.lower(): r for r in allowed_roles}
    valid_roles = [allowed_lower[r.lower()] for r in parsed.mapped_roles if r.lower() in allowed_lower]
 
    updates = {
        'role': ", ".join(valid_roles),
        'company': job['company'] or parsed.company_name,
        'experience': parsed.experience_level if parsed.experience_level in EXPERIENCE_LEVELS else '',
        'title': job['title'] or parsed.title,
        'processed': 1,
    }
 
    is_remote = job['is_remote'] if job['is_remote'] is not None else (1 if parsed.is_remote else 0)
    updates['is_remote'] = is_remote
 
    if is_remote == 0 and job['city'] is None:
        updates['city'] = resolve_city_id(db, parsed.country_code, parsed.city_name)
 
    return updates
  

async def process_unprocessed_vacancies(db):
    allowed_roles = get_allowed_roles(db)
    if not allowed_roles:
        print("No submissions yet - nothing to map roles against.")
        return

    unprocessed = db.execute("SELECT * FROM vacancies WHERE processed = 0").fetchall()
    if not unprocessed:
        print("Nothing to process.")
        return
    print(f"Processing {len(unprocessed)} vacancies (up to {MAX_CONCURRENCY} concurrent LLM calls)...")


    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    parsed_results = await asyncio.gather(
        *[extract_job(job, allowed_roles, semaphore) for job in unprocessed]
    )

    for job, parsed in zip(unprocessed, parsed_results):
        if parsed is None:
            continue
        try:
            updates = apply_extraction(db, job, parsed, allowed_roles)
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            db.execute(f"UPDATE vacancies SET {set_clause} WHERE id = ?", [*updates.values(), job['id']])
            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"Failed to save vacancy {job['id']}: {exc}")


def fetch_vacancies(db):
    spirejob(db)
    linkedin(db)


if __name__ == "__main__":
    db = get_db()
    fetch_vacancies(db)
    asyncio.run(process_unprocessed_vacancies(db))
    db.close()
