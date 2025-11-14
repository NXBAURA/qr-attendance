# app2.py - cleaned: no debug, formatted timestamps, export tidy XLSX/CSV (cid recorded but not exported)
import streamlit as st
from pathlib import Path
import qrcode
from io import BytesIO
import csv, json, os, time, urllib.parse, hashlib, uuid
from datetime import datetime
import pandas as pd

# ----------- page & SHA -----------
st.set_page_config(page_title="QR Attendance", layout="centered")
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app2.py SHA: {sha}")

# ----------- secrets & config -----------
QR_SECRET = st.secrets.get("QR_SECRET", "changeme")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"
SLOT_FILE = DATA_DIR / "current_slot.json"
SLOT_TTL = 300  # seconds

# ----------- helpers -----------
def now_iso_utc():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def atomic_write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

def read_slot_file(path: Path):
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def ensure_current_slot(ttl=SLOT_TTL):
    now_ts = int(time.time())
    data = read_slot_file(SLOT_FILE)
    if data and isinstance(data, dict):
        slot = data.get("slot_key")
        created = int(data.get("created", 0))
        if slot and (now_ts - created) <= ttl:
            return slot, created
    new_slot = uuid.uuid4().hex
    new_data = {"slot_key": new_slot, "created": now_ts}
    try:
        atomic_write_json(SLOT_FILE, new_data)
    except Exception:
        with open(SLOT_FILE, "w", encoding="utf-8") as f:
            json.dump(new_data, f)
    return new_slot, now_ts

def build_link(slot_key: str, cid: str = None):
    params = {"key": slot_key, "s": QR_SECRET}
    if cid:
        params["cid"] = cid
    return f"{BASE_URL}/?{urllib.parse.urlencode(params)}"

def make_qr_bytes(link: str):
    img = qrcode.make(link)
    b = BytesIO(); img.save(b, format="PNG"); b.seek(0)
    return b

def safe_append_csv(row: dict):
    try:
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        exists = CSV_PATH.exists()
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not exists:
                writer.writeheader(); f.flush(); os.fsync(f.fileno())
            writer.writerow(row); f.flush(); os.fsync(f.fileno())
        return True, ""
    except Exception as e:
        return False, str(e)

def read_df():
    if not CSV_PATH.exists():
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])
    try:
        return pd.read_csv(CSV_PATH)
    except Exception:
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])

def df_for_admin_display(df: pd.DataFrame):
    # copy only the useful columns and format timestamp
    if df.empty:
        return df
    df2 = df.copy()
    # convert ISO timestamp to local datetime string
    def fmt_ts(x):
        try:
            dt = datetime.fromisoformat(x.replace("Z", ""))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return x
    if "timestamp" in df2.columns:
        df2["timestamp"] = df2["timestamp"].apply(fmt_ts)
    # select visible columns in order
    cols = [c for c in ["timestamp","slot_key","name","email"] if c in df2.columns]
    return df2[cols]

def df_to_excel_bytes(df: pd.DataFrame):
    # produce an Excel file with human-friendly timestamp column
    df2 = df_for_admin_display(df)
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df2.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0)
    return bio.getvalue()

# ----------- shared slot ----------
slot_key, slot_created = ensure_current_slot(SLOT_TTL)
expires_in = int(SLOT_TTL - (time.time() - slot_created))
canonical_link = build_link(slot_key)

# ----------- UI (simplified) -----------
st.title("📋 QR Attendance Marker")

col1, col2 = st.columns([1,1])
with col1:
    st.subheader("Admin — Current QR")
    st.write("Current slot key:", f"`{slot_key}`")
    st.write(f"QR refreshes every 5 minutes • refresh in **{expires_in}s**")
    st.image(make_qr_bytes(canonical_link), width=220, caption="Scan this QR with camera")
    st.markdown(f"[Open direct attendance link]({canonical_link})")
    # open new tab & copy buttons
    js = f"""
    <button onclick="window.open('{canonical_link}','_blank')" style="padding:8px 10px;margin-right:8px;">Open in new tab</button>
    <button id="copyBtn">Copy link</button>
    <script>
      document.getElementById('copyBtn').onclick = async function() {{
        try {{ await navigator.clipboard.writeText('{canonical_link}'); this.innerText='Copied'; setTimeout(()=>this.innerText='Copy link',1200); }}
        catch(e) {{ alert('Copy failed'); }}
      }};
    </script>
    """
    st.components.v1.html(js, height=60)

with col2:
    st.markdown("### Open on this device (mobile-safe)")
    js2 = f"""
    <script>
      function getCid() {{
        try {{
          let cid = localStorage.getItem('attendance_cid');
          if(!cid){{ cid = (crypto && crypto.randomUUID)? crypto.randomUUID() : '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', cid); }}
          return cid;
        }} catch(e) {{ return '{uuid.uuid4().hex}'; }}
      }}
      function openWithCid() {{
        const cid = encodeURIComponent(getCid());
        window.location.href = '{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid=' + cid;
      }}
    </script>
    <button onclick="openWithCid()" style="padding:12px 14px;background:#2b6cb0;color:white;border:none;border-radius:8px;">Open on this device</button>
    """
    st.components.v1.html(js2, height=100)

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Use the Direct link (open in new tab on PC) or scan the QR / use 'Open on this device' on phone.")

# ----------- form handling -----------
params = st.experimental_get_query_params()
valid = False
cid = None
if "key" in params and "s" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        valid = True
        cid = params.get("cid", [None])[0]

with st.form("form"):
    name = st.text_input("Full name")
    email = st.text_input("Email")
    submit = st.form_submit_button("Mark Attendance")

if submit:
    if not name.strip() or not email.strip():
        st.error("Enter name and email.")
    elif not valid:
        st.error("You must open via the Direct link or current QR for this slot.")
    else:
        df = read_df()
        dup_cid = False
        dup_email = False
        if cid:
            dup_cid = ((df['slot_key'] == slot_key) & (df.get('cid','') == cid)).any()
        dup_email = ((df['slot_key'] == slot_key) & (df['email'].astype(str).str.lower() == email.strip().lower())).any()
        if dup_cid:
            st.error("This browser already submitted for this slot.")
        elif dup_email:
            st.error("This email already used for this slot.")
        else:
            row = {"timestamp": now_iso_utc(), "slot_key": slot_key, "name": name.strip(), "email": email.strip(), "cid": cid or ""}
            ok, err = safe_append_csv(row)
            if ok:
                st.success("Attendance marked — thank you!")
            else:
                st.error("Save failed.")
                st.text(f"Error: {err}")

# ----------- admin panel & exports -----------
st.markdown("---")
with st.expander("Admin — View / Download records (password protected)"):
    pw = st.text_input("Admin password", type="password")
    if st.button("Show"):
        if pw == ADMIN_PASSWORD:
            try:
                df = read_df()
                df_display = df_for_admin_display(df)
                if df_display.empty:
                    st.info("No records yet.")
                else:
                    st.dataframe(df_display)
                    csvb = df_display.to_csv(index=False).encode("utf-8")
                    st.download_button("Download CSV", data=csvb, file_name="attendance.csv", mime="text/csv")
                    try:
                        excel = df_to_excel_bytes(df)
                        st.download_button("Download Excel (.xlsx)", data=excel, file_name="attendance.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    except Exception as e:
                        st.error("Excel export failed.")
                        st.text(str(e))
        else:
            st.error("Wrong admin password.")
st.caption("One submission per browser per slot and one email per slot enforced. cid is stored for enforcement but not exported to reports.")
