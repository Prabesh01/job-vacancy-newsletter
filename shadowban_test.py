import requests
from bs4 import BeautifulSoup
from time import sleep

import curl_cffi

import tls_client
tls_client_session = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)

from stealth_requests import StealthSession
stealth_session = StealthSession()

shadowbanid = None

def get_first_jobid(content):
    soup = BeautifulSoup(content, "html.parser")

    jobs = soup.find_all('div',class_="base-card")

    url = jobs[0].find('a')['href']
    jid = url.split('?')[0].split('-')[-1]
    # url = f"https://www.linkedin.com/jobs/view/{jid}"
    return jid


url="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=System%20Administrator&location=worldwide&f_WT=2&start=0"
for _ in range(5): shadowbanid = get_first_jobid(requests.get(url).content)
print(f"shadowbanid: {shadowbanid}")

for i in range(10):
    print(f"Iteration-{i+1}:")

    for t in ["curl_cffi", "tls_client", "StealthSession"]:
        consecutive_429s = 0
        score=0
        j=0
        jids = []
        while True:
            j+=1
            if j>20: break
            if t=="curl_cffi":
                resp = curl_cffi.get(url, impersonate="chrome")
            elif t=="tls_client":
                resp = tls_client_session.get(url)
            else:
                resp = stealth_session.get(url)
            if resp.status_code!=200: 
                j-=1
                consecutive_429s +=1 
                wait_time = consecutive_429s * 6
                print(f"   [!] RATE LIMIT on {t} - waiting for {wait_time}s...")
                sleep(wait_time)
                continue
            consecutive_429s = 0
            jid = get_first_jobid(resp.content)
            jids.append(jid)
            if jid!=shadowbanid: score+=1
        print(f"   - {t}: {score}")
        print(jids)

    print("\n###############################\n")
