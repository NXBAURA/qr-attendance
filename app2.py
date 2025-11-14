# app2.py - Enforce cid-only opening. No "open without cid" or direct raw link.
import streamlit as st
from pathlib import Path
import qrcode
from io import BytesIO
import csv
import json
import os
import time
import urllib.parse
import hashlib
import uuid
from datetime import datetime
import pandas as pd
import shutil

# ---------- config ----------
st.set_page_config(page_title="QR Attendance", layout="centered")
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app2.py SHA: {sha}")

# ---------- secrets & paths ----------
QR_SECRET = st.secrets.get("QR_SECRET", "changeme")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"
SLOT_FILE = DATA_DIR / "current_slot.json"
ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
SLOT_TTL = 300  # seconds

# ---------- helpers ----------
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
        try:
            with open(SLOT_FILE, "w", encoding="utf-8") as f:
                json.dump(new_data, f)
        except Exception:
            pass
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
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH, index=False)
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])
    try:
        return pd.read_csv(CSV_PATH)
    except Exception:
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])

def df_for_admin_display(df: pd.DataFrame):
    if df.empty: return df
    df2 = df.copy()
    def fmt_ts(x):
        try:
            dt = datetime.fromisoformat(str(x).replace("Z",""))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return x
    if "timestamp" in df2.columns:
        df2["timestamp"] = df2["timestamp"].apply(fmt_ts)
    cols = [c for c in ["timestamp","slot_key","name","email"] if c in df2.columns]
    return df2[cols]

def df_to_excel_bytes(df: pd.DataFrame):
    df2 = df_for_admin_display(df)
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df2.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0)
    return bio.getvalue()

def archive_records():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"attendance_archive_{ts}.csv"
    try:
        if CSV_PATH.exists():
            shutil.move(str(CSV_PATH), str(dest))
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH, index=False)
        return True, str(dest)
    except Exception as e:
        return False, str(e)

def clear_records():
    try:
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH, index=False)
        return True, ""
    except Exception as e:
        return False, str(e)

# ---------- shared slot ----------
slot_key, slot_created = ensure_current_slot(SLOT_TTL)
expires_in = int(SLOT_TTL - (time.time() - slot_created))
canonical_link = build_link(slot_key)  # note: no-cid raw link kept internally but not shown for direct use

# ---------- UI ----------
st.title("📋 QR Attendance (cid required)")

left, right = st.columns([1,1])

with left:
    st.subheader("Admin — Current QR")
    st.write("Current slot key:", f"`{slot_key}`")
    st.write(f"QR refreshes every 5 minutes • refresh in **{expires_in}s**")
    st.image(make_qr_bytes(canonical_link), width=220, caption="Scan this QR with camera")
    st.markdown("**Open / copy links below will always attach your browser's device id (cid).**")

    # Open in new tab WITH cid, and copy-with-cid buttons (JS obtains localStorage cid)
    js = f"""
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button id="openWithCid" style="padding:8px 10px;border-radius:6px;background:#2b6cb0;color:white;border:none;">Open in new tab (with cid)</button>
      <button id="copyWithCid" style="padding:8px 10px;border-radius:6px;background:#718096;color:white;border:none;">Copy link (with cid)</button>
    </div>
    <script>
      function getCidLocal(){ try{ let c=localStorage.getItem('attendance_cid'); if(!c){ c=(crypto && crypto.randomUUID)?crypto.randomUUID(): '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid',c);} return c}catch(e){return '{uuid.uuid4().hex}'} }
      document.getElementById('openWithCid').onclick = function(){ const cid = encodeURIComponent(getCidLocal()); window.open("{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="+cid, "_blank"); }
      document.getElementById('copyWithCid').onclick = async function(){ try{ const cid = encodeURIComponent(getCidLocal()); const url = "{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="+cid; await navigator.clipboard.writeText(url); this.innerText='Copied'; setTimeout(()=>this.innerText='Copy link (with cid)',1200);}catch(e){alert('Copy failed')} }
    </script>
    """
    st.components.v1.html(js, height=90)

with right:
    st.markdown("### Open on this device (mobile-safe)")
    js2 = f"""
    <script>
    function getCid2(){ try{ let c=localStorage.getItem('attendance_cid'); if(!c){ c=(crypto && crypto.randomUUID)?crypto.randomUUID(): '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid',c);} return c;}catch(e){return '{uuid.uuid4().hex}'} }
    function openWithCid2(){ const cid = encodeURIComponent(getCid2()); window.location.href = '{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid=' + cid; }
    </script>
    <button onclick="openWithCid2()" style="padding:12px 14px;background:#2b6cb0;color:white;border:none;border-radius:8px;">Open on this device (with cid)</button>
    """
    st.components.v1.html(js2, height=100)

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Links without a device id (cid) are rejected. Use the buttons above so your browser includes cid, then submit once.")

# ---------- form ----------
params = st.experimental_get_query_params()
cid = None
valid = False
if "key" in params and "s" in params and "cid" in params:
    if params.get("s",[None])[0] == QR_SECRET and params.get("key",[None])[0] == slot_key:
        cid = params.get("cid",[None])[0]
        if cid and len(str(cid))>8:
            valid = True

with st.form("attendance"):
    name = st.text_input("Full name")
    email = st.text_input("Email")
    submit = st.form_submit_button("Mark Attendance")

if submit:
    if not name.strip() or not email.strip():
        st.error("Enter name and email.")
    elif not valid:
        st.error("Submission blocked: this link does not include a device identifier (cid). Use 'Open on this device' or 'Open in new tab (with cid)' above.")
    else:
        df = read_df()
        try:
            dup_cid = ((df['slot_key'] == slot_key) & (df.get('cid','') == cid)).any()
        except Exception:
            dup_cid = False
        if dup_cid:
            st.error("This device (browser) has already submitted attendance for this slot.")
        else:
            row = {"timestamp": now_iso_utc(), "slot_key": slot_key, "name": name.strip(), "email": email.strip(), "cid": cid or ""}
            ok, err = safe_append_csv(row)
            if ok:
                st.success("Attendance marked — thank you!")
            else:
                st.error("Save failed.")
                st.text(f"Error: {err}")

st.markdown("---")
with st.expander("Admin — View / Archive / Clear records (password protected)"):
    pw = st.text_input("Admin password", type="password")
    if st.button("Show records"):
        if pw == ADMIN_PASSWORD:
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

    st.markdown("---")
    st.write("Archive current records (moves CSV to data/archive_)")
    archive_confirm = st.text_input("Type ARCHIVE to confirm (case-sensitive)", key="archive_confirm")
    if st.button("Archive now"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password above first.")
        elif archive_confirm != "ARCHIVE":
            st.warning("Type ARCHIVE (exact) to confirm before archiving.")
        else:
            ok, info = archive_records()
            if ok:
                st.success(f"Archived to: {info}")
            else:
                st.error(f"Archive failed: {info}")

    st.markdown("Clear current records (delete all and start fresh)")
    clear_confirm = st.text_input("Type CLEAR to confirm (case-sensitive)", key="clear_confirm")
    if st.button("Clear now"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password above first.")
        elif clear_confirm != "CLEAR":
            st.warning("Type CLEAR (exact) to confirm before clearing.")
        else:
            ok, info = clear_records()
            if ok:
                st.success("Current records cleared — new empty file created.")
            else:
                st.error(f"Clear failed: {info}")

st.caption("Notes: cid is recorded for enforcement but not shown in admin exports. Use Archive to keep backups before clearing.")
