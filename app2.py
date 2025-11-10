# app2.py (replace your file with this)
import streamlit as st
import pandas as pd
import qrcode
from PIL import Image
import io, os, hashlib, datetime

# ---------------- CONFIG ----------------
st.set_page_config(page_title="QR Attendance", layout="wide")
SLOT_MINUTES = 5
# SECRET & ADMIN_PASSWORD should be set in Streamlit secrets or environment
SECRET = st.secrets.get("QR_SECRET", os.environ.get("QR_SECRET", "change_this_secret"))
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", os.environ.get("ADMIN_PASSWORD", "admin123"))
BASE_URL = st.secrets.get("BASE_URL", os.environ.get("BASE_URL", "http://localhost:8501"))

# ---------------- HELPERS ----------------
def now():
    return datetime.datetime.now()

def slot_index(dt=None, minutes=SLOT_MINUTES):
    dt = dt or now()
    return int(dt.timestamp() // (minutes * 60))

def slot_key_for_index(idx, secret=SECRET):
    data = f"{idx}:{secret}"
    return hashlib.sha256(data.encode()).hexdigest()[:20]

def make_qr_image(text, box_size=8, border=2):
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    return img

def downloads_path():
    # cross-platform path to Downloads
    return os.path.join(os.path.expanduser("~"), "Downloads")

def attendance_filename_for_today():
    return os.path.join(downloads_path(), f"attendance_{now().strftime('%Y-%m-%d')}.csv")

def save_attendance_row(row):
    fname = attendance_filename_for_today()
    header = not os.path.exists(fname)
    df = pd.DataFrame([row])
    df.to_csv(fname, mode="a", header=header, index=False)

def load_attendance_all():
    # load today's file only (keeps things simple)
    fname = attendance_filename_for_today()
    if os.path.exists(fname):
        return pd.read_csv(fname)
    return pd.DataFrame(columns=["timestamp","slot_index","slot_key","name","email"])

def seconds_until_next_slot(minutes=SLOT_MINUTES):
    i = slot_index()
    return int((i + 1) * (minutes * 60) - int(now().timestamp()))

def already_marked(email, slot_idx):
    df = load_attendance_all()
    if df.empty: return False
    return ((df["email"].astype(str).str.lower() == email.lower()) & (df["slot_index"] == slot_idx)).any()

# ---------------- CSS (cleaner look) ----------------
st.markdown(
    """
    <style>
    .stApp { background-color: #0f1113; color: #ddd; }
    .card { padding: 18px; border-radius: 10px; background: #0b1112; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }
    .muted { color: #a6a6a6; font-size:14px; }
    .small { font-size:13px; color:#cfcfcf; }
    .qr-col { display:flex; align-items:center; justify-content:center; flex-direction:column; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- UI ----------------
st.title("📋 QR Attendance Marker")
col_left, col_right = st.columns([1, 1.4])

# Left: admin QR
with col_left:
    st.subheader("Admin — Current QR")
    cur_idx = slot_index()
    cur_key = slot_key_for_index(cur_idx)
    link = f"{BASE_URL}/?key={cur_key}"
    img = make_qr_image(link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    st.image(buf.getvalue(), width=260)
    st.caption(f"Current slot key: `{cur_key}`")
    st.write(f"QR refreshes every {SLOT_MINUTES} minutes • refresh in **{seconds_until_next_slot()}s**", unsafe_allow_html=True)

    # Admin unlock
    if "admin_unlocked" not in st.session_state:
        st.session_state.admin_unlocked = False

    pw = st.text_input("Admin password (to view records)", type="password")
    if st.button("Unlock admin"):
        if pw == ADMIN_PASSWORD:
            st.session_state.admin_unlocked = True
            st.success("Admin unlocked — you can now view/download the day's attendance.")
        else:
            st.error("Wrong password")

    if st.session_state.admin_unlocked:
        st.markdown("---")
        st.subheader("Attendance (today)")
        df = load_attendance_all()
        st.write(f"Records for: **{now().strftime('%Y-%m-%d')}**")
        if df.empty:
            st.info("No records yet.")
        else:
            st.dataframe(df.sort_values("timestamp", ascending=False).reset_index(drop=True))
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV (today)", data=csv, file_name=os.path.basename(attendance_filename_for_today()), mime="text/csv")
        # Optional: lock admin out button
        if st.button("Lock admin"):
            st.session_state.admin_unlocked = False
            st.experimental_rerun()

# Right: public form
with col_right:
    st.subheader("Mark Your Attendance")
    st.info("Scan the admin QR (or open the link). Only the QR key in the link will be accepted for the current slot.")
    # use modern API
    qparams = st.query_params
    key_from_url = qparams.get("key", [None])[0] if isinstance(qparams.get("key", None), list) else qparams.get("key", None)
    # UI fields
    name = st.text_input("Full Name")
    email = st.text_input("Email")

    if st.button("Mark Attendance"):
        if not name or not email:
            st.error("Please fill in all fields.")
        elif key_from_url is None:
            st.error("No key detected. Open the link that contains `?key=...` or scan the admin QR.")
        else:
            # accept small drift (current slot)
            accepted = {slot_key_for_index(i) for i in range(slot_index() - 1, slot_index() + 2)}
            if key_from_url not in accepted:
                st.error("Invalid or expired QR key. Re-scan the admin QR.")
            else:
                sidx = slot_index()
                if already_marked(email, sidx):
                    st.warning("You already marked attendance for this slot.")
                else:
                    row = {
                        "timestamp": now().strftime("%Y-%m-%d %H:%M:%S"),
                        "slot_index": sidx,
                        "slot_key": key_from_url,
                        "name": name.strip(),
                        "email": email.strip().lower()
                    }
                    save_attendance_row(row)
                    st.success(f"Attendance saved for {name} at {row['timestamp']}.\nSaved to your Downloads folder.")


st.markdown("---")
st.markdown("<div class='small'>Files saved to your Downloads folder with filename like <code>attendance_YYYY-MM-DD.csv</code>. Only the admin (password) can view the list/download. If you deploy to Streamlit Cloud, store secrets there (QR_SECRET, ADMIN_PASSWORD, BASE_URL).</div>", unsafe_allow_html=True)
