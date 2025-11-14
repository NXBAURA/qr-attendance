# app.py - QR Attendance Marker (robust, responsive, CSV + Excel download)
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

# ---------------- Page config ----------------
st.set_page_config(
    page_title="QR Attendance",
    layout="centered",
    initial_sidebar_state="auto",
)

# Show file SHA (helps confirm deployed version)
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app.py SHA: {sha}")

# ---------------- Config / Secrets ----------------
QR_SECRET = st.secrets.get("QR_SECRET", "dev-secret")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")

# ---------------- Data paths ----------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"

# ---------------- Utils ----------------
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
    """
    Append row to CSV safely. Ensures parent exists and writes header when creating file.
    Returns (True, "") or (False, error_text).
    """
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

def read_attendance_df(path: Path = CSV_PATH):
    """Return a pandas DataFrame or empty DataFrame if file missing."""
    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "slot_key", "name", "email"])
    try:
        return pd.read_csv(path)
    except Exception:
        # fallback: attempt to read with csv module
        with open(path, "r", encoding="utf-8") as f:
            return pd.read_csv(f)

def df_to_excel_bytes(df: pd.DataFrame):
    """Return in-memory Excel file bytes from DataFrame."""
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0)
    return bio.getvalue()

# ---------------- Slot rotation (5 minutes) ----------------
SLOT_TTL = 300  # seconds
if "slot_key" not in st.session_state:
    st.session_state.slot_key = gen_slot_key()
    st.session_state.slot_created = time.time()

if time.time() - st.session_state.slot_created > SLOT_TTL:
    st.session_state.slot_key = gen_slot_key()
    st.session_state.slot_created = time.time()

slot_key = st.session_state.slot_key
expires_in = int(SLOT_TTL - (time.time() - st.session_state.slot_created))

# ---------------- Responsive layout ----------------
st.title("📋 QR Attendance Marker")

# Two-column layout but adapts: on narrow screens columns stack automatically
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Admin — Current QR")
    st.write("Current slot key:", f"`{slot_key}`")
    st.write(f"QR refreshes every 5 minutes • refresh in **{expires_in}s**")
    qr_link = build_qr_link(slot_key)
    st.image(make_qr_bytes(qr_link), width=220, caption="Scan this QR with phone camera")
    st.write("Or open link:", qr_link)

with col2:
    # Big friendly button for mobile devices to set correct query params on current device
    st.markdown("### Quick open (mobile friendly)")
    st.write("If scanning or copying the link caused a broken/mangled URL, use this button on the device you want to submit from.")
    if st.button("Open attendance link on this device"):
        st.experimental_set_query_params(key=slot_key, s=QR_SECRET)
        st.experimental_rerun()

    st.write("Manual: paste this query after the app URL on your phone:")
    st.code(f"?key={slot_key}&s={QR_SECRET}")

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Open the link from the QR or use the button above to ensure the link has the correct params for this slot.")

# ---------------- Validate incoming query params ----------------
params = st.experimental_get_query_params()
valid_qr = False
if "key" in params and "s" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        valid_qr = True
    else:
        st.warning("QR is invalid or expired. Use the latest QR or click the 'Open attendance link on this device' button.")

# ---------------- Attendance form (compact) ----------------
with st.form("attendance_form"):
    name = st.text_input("Full name", max_chars=80)
    email = st.text_input("Email", max_chars=120)
    submitted = st.form_submit_button("Mark Attendance")

if submitted:
    if not name.strip() or not email.strip():
        st.error("Please enter both name and email.")
    elif not valid_qr:
        st.error("You must open the form via the valid admin QR link for this slot. Use the button above if needed.")
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
            st.error("Failed to save attendance. See debug below.")
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
                    st.info("No attendance records found.")
                else:
                    # Show responsive dataframe
                    st.dataframe(df)
                    # CSV download
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    st.download_button("Download CSV", data=csv_bytes, file_name="attendance.csv", mime="text/csv")
                    # Excel download
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

st.caption("Data is stored in the app's `data/attendance.csv` file. Use the admin downloads above to get CSV or Excel.")

# ---------------- Footer / debug ----------------
st.markdown("---")
st.caption("If you encounter errors, check Streamlit logs (Manage app → Logs) and paste any 'Attempted file' + 'Error' lines here for fast help.")
