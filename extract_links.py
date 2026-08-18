import csv
import re

url_pattern = re.compile(r"(?:https?://|www\.)[^\s<>\")\]]+")
md_pattern = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")

urls = []

with open('data/messages.csv', mode='r', newline='', encoding='utf-8') as file:
    reader = csv.DictReader(file)

    for row in reader:
        msg = row.get('Message', '') or ''
        for m in md_pattern.findall(msg):
            urls.append(m)
        for m in url_pattern.findall(msg):
            urls.append(m)

seen = set()
unique_urls = []
for u in urls:
    if u not in seen:
        seen.add(u)
        unique_urls.append(u)

with open("data/messages.txt", "w", encoding='utf-8') as file:
    for u in unique_urls:
        file.write(u + "\n")
