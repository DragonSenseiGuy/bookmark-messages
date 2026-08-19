import os
import shutil
import sqlite3
import csv
import re


def retrieve():
    os.makedirs("data", exist_ok=True)
    shutil.copy(os.path.expanduser("~/Library/Messages/chat.db"), "data/chat.db")

    db = os.path.expanduser("data/chat.db")
    out = os.path.expanduser("data/messages.csv")
    with sqlite3.connect(db) as conn, open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Sender", "Message"])
        q = """SELECT datetime(m.date/1e9 + 978307200, 'unixepoch', 'localtime'), CASE m.is_from_me WHEN 1 THEN 'Me' ELSE h.id END, m.text, m.attributedBody
               FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
               LEFT JOIN chat_handle_join chj ON cmj.chat_id = chj.chat_id LEFT JOIN handle h2 ON chj.handle_id = h2.ROWID
               WHERE h.id LIKE '%REDACTED_PHONE_NUMBER%' OR h2.id LIKE '%REDACTED_PHONE_NUMBER%' GROUP BY m.ROWID ORDER BY m.date ASC;"""
        for dt, snd, t, b in conn.execute(q):
            if not t and b and isinstance(b, (bytes, bytearray)) and b'NSString' in b:
                try:
                    p = b.index(b'NSString')
                    p = b.index(b'+', p) + 1
                    n = b[p]
                    p += 1
                    if n == 0x81:
                        n = int.from_bytes(b[p:p+2], 'little')
                        p += 2
                    t = b[p:p+n].decode('utf-8', 'ignore')
                except Exception:
                    pass
            t = re.sub(r'[^\x20-\x7E\s\u00A0-\uFFFF]', '', t).strip() if t else "[Media/Empty]"
            w.writerow([dt, snd, t])


if __name__ == "__main__":
    retrieve()
