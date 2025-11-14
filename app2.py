# app2.py - Refreshed: CID-only, stable, archive/clear, clean exports
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
import shutil
from datetime import datetime
import pandas as pd

# -------- page config & SHA ----------
st.set_page_config(page_title="QR Attendance", layout="centered")
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app2.py SHA: {sha}")

# -------- secrets & config (set these in Streamlit Secrets) ----------
QR_SECRET = st.secrets.get("QR_SECRET", "changeme")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")  # must match your app host exactly

# -------- file paths ----------
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"
SLOT_FILE = DATA_DIR / "current_slot.json"
ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

SLOT_TTL = 300  # 5 minutes

# -------- helpers ----------
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

def build_prefix(slot_key: str):
    # JS-friendly prefix to which we append cid client-side
    prefix = f"{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="
    # JSON-encode to safely embed as a JS string literal
    return json.dumps(prefix)

def make_qr_bytes(link: str):
    img = qrcode.make(link)
    b = BytesIO()
    img.save(b, format="PNG")
    b.seek(0)
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
            dt = datetime.fromisoformat(str(x).replace("Z", ""))
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

# -------- shared slot ----------
slot_key, slot_created = ensure_current_slot(SLOT_TTL)
expires_in = int(SLOT_TTL - (time.time() - slot_created))
# canonical link (used as QR payload). We do NOT show no-cid raw link anymore.
canonical_prefix = build_prefix(slot_key)  # JSON string of prefix e.g. "https://...&cid="

# Make QR that points to canonical link WITHOUT cid (scanners can't run localStorage). However,
# since you asked to ALWAYS require cid, we will not accept submissions unless &cid is present.
# The QR is still useful to copy the link, but the main method to open is the buttons that attach cid.
canonical_link_no_cid = f"{BASE_URL}/?key={slot_key}&s={QR_SECRET}"

# -------- UI: simple and strict ----------
st.title("📋 QR Attendance — CID required")

left, right = st.columns([1,1])

with left:
    st.subheader("Admin — Current QR")
    st.write("Current slot key:", f"`{slot_key}`")
    st.write(f"QR refreshes every 5 minutes • refresh in **{expires_in}s**")
    st.image(make_qr_bytes(canonical_link_no_cid), width=220, caption="Scan this QR with camera (open on device using the button below)")
    st.markdown("**Important:** submissions require a device id (cid). Use the buttons to open with cid — links without cid are rejected.")

    # Buttons that always attach cid client-side
    # embed prefix (JS-safe) with json.dumps so quotes are correct
    js_prefix = canonical_prefix  # already JSON encoded string literal
    js_buttons = f"""
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <button id="openWithCid" style="padding:8px 12px;border-radius:6px;background:#2b6cb0;color:white;border:none;">Open in new tab (with cid)</button>
      <button id="copyWithCid" style="padding:8px 12px;border-radius:6px;background:#718096;color:white;border:none;">Copy link (with cid)</button>
    </div>
    <script>
      const PREFIX = {js_prefix};  // e.g. "https://...&cid="
      function ensureCid() {{
        try {{
          let c = localStorage.getItem('attendance_cid');
          if (!c) {{
            if (window.crypto && crypto.randomUUID) c = crypto.randomUUID();
            else c = 'fallbackcid_' + Math.random().toString(36).slice(2,10);
            localStorage.setItem('attendance_cid', c);
          }}
          return c;
        }} catch(e) {{
          return 'fallbackcid';
        }}
      }}
      document.getElementById('openWithCid').onclick = function() {{
        const cid = encodeURIComponent(ensureCid());
        window.open(PREFIX + cid, '_blank');
      }};
      document.getElementById('copyWithCid').onclick = async function() {{
        const cid = encodeURIComponent(ensureCid());
        const url = PREFIX + cid;
        try {{
          await navigator.clipboard.writeText(url);
          this.innerText = 'Copied';
          setTimeout(()=>this.innerText='Copy link (with cid)', 1200);
        }} catch(e) {{
          alert('Copy failed — long press to copy.');
        }}
      }};
    </script>
    """
    st.components.v1.html(js_buttons, height=90)

with right:
    st.markdown("### Open on this device (mobile-safe)")
    # open-on-this-device uses the same prefix
    js_open_same = f"""
    <script>
      const PREFIX2 = {js_prefix};
      function ensureCid2() {{
        try {{
          let c = localStorage.getItem('attendance_cid');
          if (!c) {{
            if (window.crypto && crypto.randomUUID) c = crypto.randomUUID();
            else c = 'fallbackcid_' + Math.random().toString(36).slice(2,10);
            localStorage.setItem('attendance_cid', c);
          }}
          return c;
        }} catch(e) {{
          return 'fallbackcid';
        }}
      }}
      function openWithCidSame() {{
        const cid = encodeURIComponent(ensureCid2());
        window.location.href = PREFIX2 + cid;
      }}
    </script>
    <button onclick="openWithCidSame()" style="padding:12px 14px;background:#2b6cb0;color:white;border:none;border-radius:8px;">Open on this device (with cid)</button>
    """
    st.components.v1.html(js_open_same, height=100)

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Links without a device id (cid) will be rejected. Use the buttons above; they attach cid automatically and open the valid URL.")

# -------- form handling: require cid present in query params ----------
params = st.experimental_get_query_params()
cid = None
valid = False
if "key" in params and "s" in params and "cid" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        cid = params.get("cid", [None])[0]
        # sanity check
        if cid and len(str(cid)) > 6:
            valid = True

with st.form("attendance"):
    name = st.text_input("Full name")
    email = st.text_input("Email")
    submit = st.form_submit_button("Mark Attendance")

if submit:
    if not name.strip() or not email.strip():
        st.error("Enter name and email.")
    elif not valid:
        st.error("Submission blocked: this link does not include a device identifier (cid). Use the 'Open on this device (with cid)' or 'Open in new tab (with cid)' buttons above and try again.")
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
    pw = st.text_input("Admin password", type="password", key="admin_pw")
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
