# app2.py - QR Attendance app WITHOUT writing to disk (in-memory only)
import streamlit as st, hashlib, pathlib
sha = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest() if pathlib.Path(__file__).exists() else "no-file"
st.sidebar.text(f"app2.py SHA: {sha[:12]}")

import streamlit as st
from pathlib import Path
import qrcode
from io import BytesIO, StringIO
import time
from datetime import datetime
import uuid
import urllib.parse
import csv

st.set_page_config(page_title="QR Attendance Marker", layout="centered")

# ---------- Config / Secrets ----------
QR_SECRET = st.secrets.get("QR_SECRET", "dev-secret")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")

# ---------- Session-state storage ----------
if "attendance_rows" not in st.session_state:
    st.session_state.attendance_rows = []  # list of dicts: {timestamp, slot_key, name, email}

# ---------- Helpers ----------
def now_iso_utc():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def gen_slot_key():
    return uuid.uuid4().hex

def build_qr_link(slot_key: str):
    params = {"key": slot_key, "s": QR_SECRET}
    return f"{BASE_URL}/?{urllib.parse.urlencode(params)}"

def make_qr_bytes(link: str):
    img = qrcode.make(link)
    b = BytesIO()
    img.save(b, format="PNG")
    b.seek(0)
    return b

def attendance_to_csv_string(rows):
    if not rows:
        return ""
    output = StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()

# ---------- Slot rotate ----------
SLOT_TTL = 300  # 5 minutes
if "slot_key" not in st.session_state:
    st.session_state.slot_key = gen_slot_key()
    st.session_state.slot_created = time.time()

if time.time() - st.session_state.slot_created > SLOT_TTL:
    st.session_state.slot_key = gen_slot_key()
    st.session_state.slot_created = time.time()

slot_key = st.session_state.slot_key
expires_in = int(SLOT_TTL - (time.time() - st.session_state.slot_created))

# ---------- UI ----------
st.title("📋 QR Attendance Marker")
st.subheader("Admin — Current QR")
st.write("Current slot key:", f"`{slot_key}`")
st.write(f"QR refreshes every 5 minutes • refresh in **{expires_in}s**")

with st.expander("Admin — View records (enter password)"):
    pw = st.text_input("Admin password", type="password")
    if st.button("View records"):
        if pw == ADMIN_PASSWORD:
            rows = st.session_state.attendance_rows
            if not rows:
                st.info("No attendance records in memory yet.")
            else:
                st.dataframe(rows)
                csv_str = attendance_to_csv_string(rows)
                st.download_button("Download CSV (current in-memory data)", csv_str, "attendance.csv", "text/csv")
        else:
            st.error("Wrong admin password.")

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Scan the admin QR (or open the link). Only the QR key in the link will be accepted for the current slot.")

# show QR
qr_link = build_qr_link(slot_key)
st.image(make_qr_bytes(qr_link), width=220, caption="Scan this QR or open link below.")
st.write("Or open link:", qr_link)

# check query params (user came via QR)
params = st.experimental_get_query_params()
valid_qr = False
if "key" in params and "s" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        valid_qr = True
    else:
        st.warning("QR is invalid or expired. Use the latest QR from the admin panel.")

with st.form("attendance_form"):
    name = st.text_input("Full Name")
    email = st.text_input("Email")
    submitted = st.form_submit_button("Mark Attendance")

if submitted:
    if not name.strip() or not email.strip():
        st.error("Enter both name and email.")
    elif not valid_qr:
        st.error("You must open the form via a valid admin QR link for this slot.")
    else:
        row = {
            "timestamp": now_iso_utc(),
            "slot_key": slot_key,
            "name": name.strip(),
            "email": email.strip()
        }
        st.session_state.attendance_rows.append(row)
        st.success("Attendance marked — thank you!")
        # show count
        st.info(f"Total records in memory: {len(st.session_state.attendance_rows)}")

st.caption("NOTE: This version does not save data to disk. Data is kept in-memory and will be lost if the app restarts. If you need persistent storage, I can add Google Sheets / Airtable / remote DB next.")
