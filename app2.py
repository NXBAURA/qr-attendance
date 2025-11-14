# app2.py - Auto-cid QR Attendance (error-free version)
import streamlit as st
from pathlib import Path
from io import BytesIO
import qrcode
import csv
import json
import os
import time
import urllib.parse
import hashlib
import uuid
import shutil
from datetime import datetime
import pandas as pd

# ---------- Page config ----------
st.set_page_config(page_title="QR Attendance", layout="centered")

# ---------- Secrets ----------
QR_SECRET = st.secrets["QR_SECRET"]
ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
BASE_URL = st.secrets["BASE_URL"]

# ---------- Paths ----------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"
SLOT_FILE = DATA_DIR / "current_slot.json"
ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)

SLOT_TTL = 300  # 5 mins shared slot

# ---------- Utils ----------
def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def local_fmt(iso_z):
    try:
        dt = datetime.fromisoformat(iso_z.replace("Z", ""))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return iso_z

def write_json_atomic(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

def read_json(path):
    if not path.exists(): return None
    try:
        return json.load(open(path, "r"))
    except:
        return None

def ensure_slot():
    now = int(time.time())
    data = read_json(SLOT_FILE)
    if data and (now - data["created"] <= SLOT_TTL):
        return data["slot_key"], data["created"]
    slot = uuid.uuid4().hex
    write_json_atomic(SLOT_FILE, {"slot_key": slot, "created": now})
    return slot, now

slot_key, created = ensure_slot()
expires = SLOT_TTL - (int(time.time()) - created)

def build_link(slot_key, cid=None):
    params = {"key": slot_key, "s": QR_SECRET}
    if cid: params["cid"] = cid
    return BASE_URL + "/?" + urllib.parse.urlencode(params)

def qr_bytes(link):
    img = qrcode.make(link)
    b = BytesIO()
    img.save(b, format="PNG")
    b.seek(0)
    return b

def read_df():
    if not CSV_PATH.exists():
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH, index=False)
    try:
        return pd.read_csv(CSV_PATH)
    except:
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])

def save_row(row):
    try:
        exists = CSV_PATH.exists()
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not exists: writer.writeheader()
            writer.writerow(row)
            f.flush(); os.fsync(f.fileno())
        return True, ""
    except Exception as e:
        return False, str(e)

def archive():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"att_{ts}.csv"
    try:
        if CSV_PATH.exists(): shutil.move(str(CSV_PATH), str(dest))
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH, index=False)
        return True, str(dest)
    except Exception as e:
        return False, str(e)

# ---------- UI ----------
st.title("📋 QR Attendance Marker")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Admin – Current QR")
    st.write(f"Current slot key: `{slot_key}`")
    st.write(f"Refreshes in **{expires}s**")

    canonical = build_link(slot_key)
    st.image(qr_bytes(canonical), width=240)

with col2:
    st.markdown("### Open with CID (auto)")
    st.markdown("When scanning QR, CID will be auto-created and auto-added.\nNo buttons needed.")

st.markdown("---")

# ---------- AUTO CID INJECT / REDIRECT ----------
params = st.experimental_get_query_params()

key_ok = params.get("key", [""])[0] == slot_key
s_ok   = params.get("s", [""])[0] == QR_SECRET
has_cid = "cid" in params

if key_ok and s_ok and not has_cid:
    # NO F-STRING EXPRESSIONS INSIDE JS = NO ERRORS
    js = """
    <script>
    (function(){
      try {
        let cid = localStorage.getItem('attendance_cid');
        if (!cid) {
          cid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : 'fallbackcid123456789';
          localStorage.setItem('attendance_cid', cid);
        }
        const p = new URLSearchParams(window.location.search);
        p.set('cid', cid);
        const base = window.location.origin + window.location.pathname;
        window.location.replace(base + "?" + p.toString());
      } catch (e) {
        console.log("Auto-CID failed", e);
      }
    })();
    </script>
    """
    st.components.v1.html(js, height=1)
    st.stop()

# ---------- Now check again ----------
params = st.experimental_get_query_params()
valid = ("cid" in params and key_ok and s_ok)
cid = params.get("cid", [""])[0] if valid else None

# ---------- FORM ----------
st.header("Mark Attendance")

with st.form("attend"):
    name = st.text_input("Full Name")
    email = st.text_input("Email")
    sub = st.form_submit_button("Submit")

if sub:
    if not name.strip() or not email.strip():
        st.error("Fill all fields.")
    elif not valid:
        st.error("Invalid or missing CID. (Auto-CID failed. Try scanning QR again.)")
    else:
        df = read_df()

        # block duplicate from same cid same slot
        if ((df["slot_key"] == slot_key) & (df["cid"] == cid)).any():
            st.error("This device already submitted for this slot.")
        else:
            ok, err = save_row({
                "timestamp": now_iso(),
                "slot_key": slot_key,
                "name": name,
                "email": email,
                "cid": cid
            })
            if ok:
                st.success("Attendance marked!")
            else:
                st.error("Save failed: " + err)

# ---------- ADMIN ----------
st.markdown("---")
with st.expander("Admin"):
    pw = st.text_input("Password", type="password")
    if pw == ADMIN_PASSWORD:
        df = read_df()
        if st.button("Show Records"):
            if df.empty: st.info("No entries.")
            else:
                df2 = df.copy()
                df2["timestamp"] = df2["timestamp"].apply(local_fmt)
                st.dataframe(df2[["timestamp","slot_key","name","email"]])

                st.download_button(
                    "Download CSV",
                    df2.to_csv(index=False),
                    "attendance.csv"
                )

        st.write("Archive / Clear")
        if st.button("Archive"):
            ok, info = archive()
            st.success("Archived: " + info if ok else "Error: " + info)
    else:
        if pw:
            st.error("Wrong password")
