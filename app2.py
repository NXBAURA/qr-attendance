# app.py - Robust QR attendance with per-browser lock (cid) + CSV & Excel downloads
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
import pandas as pd
import html

# ---------------- page config ----------------
st.set_page_config(page_title="QR Attendance", layout="centered")

# show file SHA in sidebar so you can confirm deployment
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app.py SHA: {sha}")

# ---------------- config / secrets ----------------
QR_SECRET = st.secrets.get("QR_SECRET", "dev-secret")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")

# ---------------- data paths ----------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"

# ---------------- helpers ----------------
def now_iso_utc():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def gen_slot_key():
    return uuid.uuid4().hex

def build_qr_link(slot_key: str):
    params = {"key": slot_key, "s": QR_SECRET}
    return f"{BASE_URL}/?{urllib.parse.urlencode(params)}"

def make_qr_bytes(link: str, box_size=6):
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
                f.flush(); os.fsync(f.fileno())
            writer.writerow(row)
            f.flush(); os.fsync(f.fileno())
        return True, ""
    except Exception as e:
        return False, str(e)

def read_attendance_df(path: Path = CSV_PATH):
    if not path.exists():
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])

def df_to_excel_bytes(df: pd.DataFrame):
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0)
    return bio.getvalue()

# ---------------- slot rotate (5 minutes) ----------------
SLOT_TTL = 300
if "slot_key" not in st.session_state:
    st.session_state.slot_key = gen_slot_key()
    st.session_state.slot_created = time.time()

if time.time() - st.session_state.slot_created > SLOT_TTL:
    st.session_state.slot_key = gen_slot_key()
    st.session_state.slot_created = time.time()

slot_key = st.session_state.slot_key
expires_in = int(SLOT_TTL - (time.time() - st.session_state.slot_created))

# ---------------- UI layout ----------------
st.title("📋 QR Attendance Marker")

col1, col2 = st.columns([1,1])

with col1:
    st.subheader("Admin — Current QR")
    st.write("Current slot key:", f"`{slot_key}`")
    st.write(f"QR refreshes every 5 minutes • refresh in **{expires_in}s**")
    qr_link = build_qr_link(slot_key)
    st.image(make_qr_bytes(qr_link), width=220, caption="Scan this QR with camera")
    st.write("Or open link:", qr_link)

with col2:
    st.markdown("### Quick open (mobile-safe)")
    st.write("Click this on the device/browser you want to submit from. It will create a persistent client-id (stored in your browser) and redirect you with `?key=..&s=..&cid=..` so the server can allow one submission per browser.")
    # client-side redirect button using localStorage to store cid
    safe_js = f"""
    <script>
    // Ensure strong id in localStorage
    function getCid() {{
      let cid = localStorage.getItem("attendance_cid");
      if (!cid) {{
        // create a v4-like id
        cid = crypto.randomUUID ? crypto.randomUUID() : '{uuid.uuid4().hex}';
        localStorage.setItem("attendance_cid", cid);
      }}
      return cid;
    }}
    function redirect() {{
      const cid = encodeURIComponent(getCid());
      const key = "{slot_key}";
      const s = "{QR_SECRET}";
      const base = window.location.origin + window.location.pathname;
      const url = base + "?key=" + key + "&s=" + s + "&cid=" + cid;
      window.location.href = url;
    }}
    </script>
    <button onclick="redirect()" style="padding:12px 18px;border-radius:6px;background:#2b6cb0;color:white;border:none;font-size:15px;">
      Open attendance link on this device (mobile-safe)
    </button>
    """
    st.components.v1.html(safe_js, height=80)

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Open the link from the QR or use the button above to ensure the link has correct params for this slot.")

# ---------------- validate incoming query params ----------------
params = st.experimental_get_query_params()
valid_qr = False
cid = None
if "key" in params and "s" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        valid_qr = True
        cid = params.get("cid", [None])[0]
    else:
        st.warning("QR is invalid or expired. Use the latest QR or click the mobile-safe button.")

# ---------------- Attendance form ----------------
with st.form("attendance_form"):
    name = st.text_input("Full name", max_chars=80)
    email = st.text_input("Email", max_chars=120)
    submitted = st.form_submit_button("Mark Attendance")

if submitted:
    if not name.strip() or not email.strip():
        st.error("Please enter both name and email.")
    elif not valid_qr:
        st.error("You must open via a valid admin QR link for this slot. Use the mobile-safe button above.")
    else:
        df = read_attendance_df()
        # check duplicates: by cid for this slot OR by email for this slot
        already_cid = False
        already_email = False
        if cid:
            already_cid = ((df['slot_key'] == slot_key) & (df.get('cid', '') == cid)).any()
        already_email = ((df['slot_key'] == slot_key) & (df['email'].astype(str).str.lower() == email.strip().lower())).any()
        if already_cid:
            st.error("This browser has already submitted attendance for this slot.")
        elif already_email:
            st.error("This email has already been used to mark attendance for this slot.")
        else:
            row = {
                "timestamp": now_iso_utc(),
                "slot_key": slot_key,
                "name": name.strip(),
                "email": email.strip(),
                "cid": cid or ""
            }
            ok, err = safe_append_csv(row)
            if ok:
                st.success("Attendance marked — thank you!")
            else:
                st.error("Failed to save attendance.")
                st.text(f"Attempted file: {CSV_PATH.resolve()}")
                st.text(f"Error: {err}")

st.markdown("---")

# ---------------- Admin panel ----------------
with st.expander("Admin — View / Download records (password protected)"):
    pw = st.text_input("Admin password", type="password")
    if st.button("Show records"):
        if pw == ADMIN_PASSWORD:
            try:
                df = read_attendance_df()
                if df.empty:
                    st.info("No attendance records yet.")
                else:
                    st.dataframe(df)
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    st.download_button("Download CSV", data=csv_bytes, file_name="attendance.csv", mime="text/csv")
                    try:
                        excel_bytes = df_to_excel_bytes(df)
                        st.download_button("Download Excel (.xlsx)", data=excel_bytes, file_name="attendance.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    except Exception as e:
                        st.error("Excel export failed.")
                        st.text(str(e))
            except Exception as e:
                st.error("Failed to load records.")
                st.text(str(e))
        else:
            st.error("Wrong admin password.")

st.caption("Data saved to data/attendance.csv. Each row includes 'cid' (browser id). One submission per browser per slot and one submission per email per slot are enforced.")

# small footer
st.markdown("---")
st.caption("If you still see errors, open Manage app → Logs and paste the last 'Attempted file' and 'Error' lines here.")
