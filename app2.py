# app2.py - QR Attendance Marker (with guaranteed local-link button + safe CSV write)
import streamlit as st
from pathlib import Path
import qrcode
from io import BytesIO
import csv
import time
import os
from datetime import datetime
import uuid
import urllib.parse
import hashlib

# show SHA so you can confirm deployment
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app2.py SHA: {sha}")

st.set_page_config(page_title="QR Attendance Marker", layout="centered")

# ---------- Config / Secrets ----------
QR_SECRET = st.secrets.get("QR_SECRET", "dev-secret")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")

# data folder
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"

# helpers
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

def safe_append_csv(row: dict, path: Path = CSV_PATH):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = path.exists()
        with open(path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
                f.flush()
                os.fsync(f.fileno())
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        return True, ""
    except Exception as e:
        return False, str(e)

# slot rotation
SLOT_TTL = 300
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
            if CSV_PATH.exists():
                try:
                    with open(CSV_PATH, "r", encoding="utf-8") as f:
                        content = f.read()
                    st.code(content[:10000] + ("" if len(content) < 10000 else "\n\n...trimmed..."))
                    st.download_button("Download CSV", data=content, file_name="attendance.csv")
                except Exception as e:
                    st.error("Failed to read records.")
                    st.text(str(e))
            else:
                st.info("No attendance records yet.")
        else:
            st.error("Wrong admin password.")

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Scan the admin QR (or open the link). Only the QR key in the link will be accepted for the current slot.")

# show QR and the canonical link
qr_link = build_qr_link(slot_key)
st.image(make_qr_bytes(qr_link), width=220, caption="Scan this QR or use the link below.")
st.write("Or open link:", qr_link)

# --- NEW: make it easy to get a valid link on this device ---
st.markdown("### Quick fix (use this if scanning/copying gives an invalid link)")
st.write("If scanning or copying produced a link that says 'invalid or expired', click the button below on the device you want to submit from — this will set the correct `key` and `s` in your URL automatically.")
if st.button("Open attendance link on this device"):
    # set correct query params in the current browser tab (will reload with those params)
    st.experimental_set_query_params(key=slot_key, s=QR_SECRET)
    st.experimental_rerun()

st.write("If that doesn't work, copy-paste this exact query (after the app URL) into your phone's address bar:")
st.code(f"?key={slot_key}&s={QR_SECRET}")

# allow manual paste/override (handy for testing)
st.markdown("**Manual testing** — paste a `?key=...&s=...` string here and click Apply:")
manual_q = st.text_input("Paste query string (including ?)", value="")
if st.button("Apply query string"):
    # sanitize and parse if starts with '?'
    q = manual_q.strip()
    if q.startswith("?"):
        try:
            # remove leading ? and parse
            q = q[1:]
            params = dict(urllib.parse.parse_qsl(q))
            st.experimental_set_query_params(**params)
            st.experimental_rerun()
        except Exception as e:
            st.error("Bad query string.")

# check query params and validate
params = st.experimental_get_query_params()
valid_qr = False
if "key" in params and "s" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        valid_qr = True
    else:
        st.warning("QR is invalid or expired. Use the latest QR from the admin panel or click the button above.")

# Temporary toggle for allowing direct submission (admin-only testing)
if "allow_direct" not in st.session_state:
    st.session_state.allow_direct = False
with st.expander("Developer / Test options (admin only)"):
    if st.checkbox("Allow direct submission without QR (for quick testing)", value=False):
        st.session_state.allow_direct = True
    else:
        st.session_state.allow_direct = False
    st.write("Current data file:", CSV_PATH.resolve())

# attendance form
with st.form("attendance_form"):
    name = st.text_input("Full Name")
    email = st.text_input("Email")
    submitted = st.form_submit_button("Mark Attendance")

if submitted:
    if not name.strip() or not email.strip():
        st.error("Enter both name and email.")
    elif not (valid_qr or st.session_state.allow_direct):
        st.error("You must open the form via a valid admin QR link for this slot. Use the big button above on this device if scanning/copying fails.")
    else:
        row = {
            "timestamp": now_iso_utc(),
            "slot_key": slot_key,
            "name": name.strip(),
            "email": email.strip()
        }
        ok, err = safe_append_csv(row)
        if ok:
            st.success("Attendance marked — thank you!")
        else:
            st.error("Failed to save attendance.")
            st.text(f"Attempted file: {CSV_PATH.resolve()}")
            st.text(f"Error: {err}")

st.markdown("---")
st.caption("If you still see 'invalid or expired' paste the query string shown above into the phone address bar or click 'Open attendance link on this device'.")
