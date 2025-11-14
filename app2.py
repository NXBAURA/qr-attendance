# app2.py - QR attendance with teacher-controlled PIN (drop-in)
# - Slot TTL = 10 minutes
# - Admin can Set / Generate / Clear PIN per-slot
# - Students must enter current slot PIN to submit
# - Auto-CID injection + fallback; Open-with-CID buttons
# - Admin: show, download CSV/XLSX, archive, clear
import streamlit as st
from pathlib import Path
from io import BytesIO
import qrcode, csv, json, os, time, urllib.parse, hashlib, uuid, shutil
from datetime import datetime
import pandas as pd

# -------- CONFIG ----------
SLOT_TTL = 600  # 10 minutes
ENFORCE_CID = True  # keep device-lock; set False to disable
st.set_page_config(page_title="QR Attendance", layout="wide")
# -------- SECRETS (set these in Streamlit Cloud) ----------
QR_SECRET = st.secrets.get("QR_SECRET", "changeme")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")  # match your app URL exactly

# -------- PATHS ----------
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"
SLOT_FILE = DATA_DIR / "current_slot.json"
ARCHIVE_DIR = DATA_DIR / "archive"; ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# -------- UTILITIES ----------
def now_iso_utc():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def now_local_str(iso_z):
    try:
        dt = datetime.fromisoformat(str(iso_z).replace("Z",""))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(iso_z)

def atomic_write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

def read_json_safe(path: Path):
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# -------- Slot management (slot_key + created + optional pin) ----------
def ensure_current_slot(ttl=SLOT_TTL):
    now_ts = int(time.time())
    data = read_json_safe(SLOT_FILE)
    if data and isinstance(data, dict):
        slot = data.get("slot_key"); created = int(data.get("created", 0))
        if slot and (now_ts - created) <= ttl:
            return slot, created
    # create new slot (clears PIN)
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

def read_slot_data():
    d = read_json_safe(SLOT_FILE) or {}
    return d

def write_slot_data(updates: dict):
    data = read_slot_data()
    data.update(updates)
    try:
        atomic_write_json(SLOT_FILE, data)
        return True
    except Exception:
        try:
            with open(SLOT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return True
        except Exception:
            return False

def get_current_pin():
    d = read_slot_data()
    pin = d.get("pin")
    return pin

def set_current_pin(pin_value: str):
    pin = str(pin_value).strip()
    if pin == "":
        # clear
        return write_slot_data({"pin": ""})
    else:
        return write_slot_data({"pin": pin, "pin_set_at": int(time.time())})

# -------- Links & QR ----------
def build_link(slot_key: str, cid: str = None):
    params = {"key": slot_key, "s": QR_SECRET}
    if cid: params["cid"] = cid
    return f"{BASE_URL}/?{urllib.parse.urlencode(params)}"

def make_qr_bytes(link: str):
    img = qrcode.make(link); b = BytesIO(); img.save(b, format="PNG"); b.seek(0)
    return b

# -------- CSV helpers ----------
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

def df_for_export(df):
    if df.empty: return df
    d = df.copy()
    if "timestamp" in d.columns:
        d["timestamp"] = d["timestamp"].apply(now_local_str)
    cols = [c for c in ["timestamp","slot_key","name","email"] if c in d.columns]
    return d.loc[:, cols]

def df_to_xlsx_bytes(df):
    bio = BytesIO()
    d = df_for_export(df)
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        d.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0); return bio.getvalue()

def archive_records():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"attendance_archive_{ts}.csv"
    try:
        if CSV_PATH.exists(): shutil.move(str(CSV_PATH), str(dest))
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

# -------- Prepare UI and slot ----------
# precompute safe fallback cids to avoid f-string-in-js issues
fallback_cid = uuid.uuid4().hex
fallback_cid2 = uuid.uuid4().hex

slot_key, slot_created = ensure_current_slot(SLOT_TTL)
expires_in = int(SLOT_TTL - (time.time() - slot_created))
canonical_link = build_link(slot_key)  # no-cid link encoded in QR

# -------- UI layout ----------
st.title("📋 QR Attendance — teacher PIN mode")
left, right = st.columns([2,1])

with right:
    st.subheader("Admin — Current QR & Controls")
    st.write("Slot key:", f"`{slot_key}`")
    st.write(f"QR slot length: **{int(SLOT_TTL/60)} minutes** • refresh in **{expires_in}s**")
    st.image(make_qr_bytes(canonical_link), width=220, caption="Scan this QR with phone camera")
    st.markdown("**Links below attach your browser's device id (cid)**")
    admin_js = f"""
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <button id="openWithCid" style="padding:8px 12px;background:#2b6cb0;color:white;border:none;border-radius:8px;">Open in new tab (with cid)</button>
      <button id="copyWithCid" style="padding:8px 12px;background:#4a5568;color:white;border:none;border-radius:8px;">Copy link (with cid)</button>
    </div>
    <script>
      function getCidLocal() {{
        try {{
          let c = localStorage.getItem('attendance_cid');
          if(!c) {{ c = (crypto && crypto.randomUUID) ? crypto.randomUUID() : "{fallback_cid}"; localStorage.setItem('attendance_cid', c); }}
          return c;
        }} catch(e) {{ return "{fallback_cid}"; }}
      }}
      document.getElementById('openWithCid').onclick = function() {{
        const cid = encodeURIComponent(getCidLocal());
        window.open("{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid=" + cid, "_blank");
      }};
      document.getElementById('copyWithCid').onclick = async function() {{
        try {{
          const cid = encodeURIComponent(getCidLocal());
          const url = "{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid=" + cid;
          await navigator.clipboard.writeText(url);
          this.innerText='Copied';
          setTimeout(()=>this.innerText='Copy link (with cid)',1200);
        }} catch(e) {{ alert('Copy failed'); }}
      }};
    </script>
    """
    st.components.v1.html(admin_js, height=90)

with left:
    st.subheader("Open on this device (mobile-safe)")
    mobile_js = f"""
    <script>
      function getCidDevice() {{
        try {{
          let c = localStorage.getItem('attendance_cid');
          if(!c) {{ c = (crypto && crypto.randomUUID) ? crypto.randomUUID() : "{fallback_cid2}"; localStorage.setItem('attendance_cid', c); }}
          return c;
        }} catch(e) {{ return "{fallback_cid2}"; }}
      }}
      function openWithCidDevice() {{
        const cid = encodeURIComponent(getCidDevice());
        window.location.href = "{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid=" + cid;
      }}
    </script>
    <button onclick="openWithCidDevice()" style="padding:12px 14px;background:#2b6cb0;color:white;border:none;border-radius:8px;">Open on this device (with cid)</button>
    """
    st.components.v1.html(mobile_js, height=100)

st.markdown("---")
st.header("Mark Your Attendance")
st.write("Students: scan QR and enter Name, Email and the current Class PIN set by the teacher. If the PIN is wrong, submission is blocked.")

# -------- Auto-CID injection + fallback (if key+s present but no cid) ----------
params = st.experimental_get_query_params()
if "key" in params and "s" in params:
    s_ok = params.get("s", [""])[0] == QR_SECRET
    key_ok = params.get("key", [""])[0] == slot_key
    if s_ok and key_ok and ("cid" not in params) and ENFORCE_CID:
        auto_html = f"""
        <div style="padding:12px;border-radius:8px;background:#111827;color:#fff;">
          <script>
            (function(){{
              try {{
                let cid = localStorage.getItem('attendance_cid');
                if(!cid) {{ cid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : "{fallback_cid}"; localStorage.setItem('attendance_cid', cid); }}
                const base = window.location.origin + window.location.pathname;
                const p = new URLSearchParams(window.location.search);
                p.set('cid', cid);
                window.location.replace(base + '?' + p.toString());
              }} catch(e) {{
                console.error('auto-cid failed', e);
              }}
            }})();
          </script>
          <div style="margin-top:10px;">
            <strong>If nothing happened, click the fallback button below to enable CID and continue.</strong>
            <div style="margin-top:10px;">
              <button id="fallbackCid" style="padding:12px 16px;background:#e53e3e;color:white;border:none;border-radius:8px;">Enable CID & Continue</button>
            </div>
          </div>
        </div>
        <script>
          document.addEventListener('DOMContentLoaded', function(){{
            var b = document.getElementById('fallbackCid');
            if(b) {{
              b.onclick = function() {{
                try {{
                  let cid = localStorage.getItem('attendance_cid');
                  if(!cid) {{ cid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : "{fallback_cid}"; localStorage.setItem('attendance_cid', cid); }}
                  const base = window.location.origin + window.location.pathname;
                  const p = new URLSearchParams(window.location.search);
                  p.set('cid', cid);
                  window.location.replace(base + '?' + p.toString());
                }} catch(ex) {{
                  alert('This viewer blocks features. Open in browser (Chrome/Firefox) and try again.');
                }}
              }};
            }}
          }});
        </script>
        """
        st.components.v1.html(auto_html, height=220)
        st.stop()

# refresh params
params = st.experimental_get_query_params()
cid = None; valid_link = False
if "key" in params and "s" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        cid = params.get("cid", [None])[0] if "cid" in params else None
        if not ENFORCE_CID or (cid and len(str(cid))>8):
            valid_link = True

# -------- Attendance form (includes PIN field) ----------
with st.form("attendance_form"):
    name = st.text_input("Full name", max_chars=80)
    email = st.text_input("Email", max_chars=120)
    pin_entered = st.text_input("Class PIN (ask teacher)", max_chars=12)
    submitted = st.form_submit_button("Mark Attendance")

if submitted:
    if not name.strip() or not email.strip():
        st.error("Enter name and email.")
    elif not valid_link:
        st.error("Submission blocked: page missing valid CID. Use Open on this device or Open in new tab (with cid).")
    else:
        # PIN check
        current_pin = get_current_pin()
        if current_pin and str(current_pin).strip() != "":
            if not pin_entered or pin_entered.strip() != str(current_pin).strip():
                st.error("Wrong PIN. Ask your teacher for the current class PIN.")
            else:
                # proceed to duplicate checks & save
                df = read_df()
                dup = False
                try:
                    dup = ((df['slot_key'] == slot_key) & (df.get('cid','') == cid)).any()
                except Exception:
                    dup = False
                if dup:
                    st.error("This device already submitted for this slot.")
                else:
                    row = {"timestamp": now_iso_utc(), "slot_key": slot_key, "name": name.strip(), "email": email.strip(), "cid": cid or ""}
                    ok, err = safe_append_csv(row)
                    if ok:
                        st.success("Attendance marked — thank you!")
                    else:
                        st.error("Save failed."); st.text(err)
        else:
            st.error("Teacher has not set a PIN for this slot. Ask the teacher to set it in Admin.")

# -------- Admin panel (password protected) ----------
st.markdown("---")
with st.expander("Admin — View / PIN / Archive / Clear (password protected)"):
    pw = st.text_input("Admin password", type="password")
    if st.button("Show records"):
        if pw == ADMIN_PASSWORD:
            df = read_df()
            view = df_for_export(df)
            if view.empty:
                st.info("No records yet.")
            else:
                st.dataframe(view)
                st.download_button("Download CSV", data=view.to_csv(index=False).encode("utf-8"), file_name="attendance.csv", mime="text/csv")
                try:
                    st.download_button("Download Excel (.xlsx)", data=df_to_xlsx_bytes(df), file_name="attendance.xlsx")
                except Exception as e:
                    st.error("Excel export failed."); st.text(str(e))
        else:
            st.error("Wrong admin password.")

    st.markdown("---")
    st.subheader("Class PIN (teacher controls for current slot)")
    current_pin = get_current_pin()
    st.write("Current PIN set for this slot:", ("`"+str(current_pin)+"`") if current_pin else "No PIN set")
    # set / generate / clear
    pin_input = st.text_input("Set PIN (4-8 chars)", key="pin_input")
    if st.button("Set PIN"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password first.")
        elif not pin_input or len(pin_input.strip()) < 2:
            st.warning("Choose a PIN of at least 2 characters.")
        else:
            ok = set_current_pin(pin_input.strip())
            if ok:
                st.success("PIN saved for current slot.")
            else:
                st.error("Failed to save PIN.")

    if st.button("Generate random 4-digit PIN"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password first.")
        else:
            rnd = str(uuid.uuid4().int)[:4]
            ok = set_current_pin(rnd)
            if ok:
                st.success(f"Generated PIN: `{rnd}` (saved for current slot)")
            else:
                st.error("Failed to save generated PIN.")

    if st.button("Clear PIN for this slot"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password first.")
        else:
            ok = set_current_pin("")
            if ok:
                st.success("PIN cleared for current slot.")
            else:
                st.error("Failed to clear PIN.")

    st.markdown("---")
    st.write("Archive current records (moves CSV to data/archive_)")
    archive_token = st.text_input("Type ARCHIVE to confirm", key="arch_token")
    if st.button("Archive now"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password first.")
        elif archive_token != "ARCHIVE":
            st.warning("Type ARCHIVE exactly to confirm.")
        else:
            ok, info = archive_records()
            if ok: st.success(f"Archived: {info}")
            else: st.error(f"Archive failed: {info}")

    st.write("Clear current records (delete all and start fresh)")
    clear_token = st.text_input("Type CLEAR to confirm", key="clear_token")
    if st.button("Clear now"):
        if pw != ADMIN_PASSWORD:
            st.error("Enter admin password first.")
        elif clear_token != "CLEAR":
            st.warning("Type CLEAR exactly to confirm.")
        else:
            ok, info = clear_records()
            if ok: st.success("Cleared current records.")
            else: st.error(f"Clear failed: {info}")

st.caption("PIN is tied to the current slot and will be cleared automatically when the slot rotates. PIN is not included in exported files.")
