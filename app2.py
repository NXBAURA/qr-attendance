# app2.py - Refurbished QR attendance app
# - Slot TTL = 10 minutes
# - Auto-CID injection + visible fallback
# - ENFORCE_CID toggle (set False to disable cid enforcement)
# - Admin panel: clickable Show / Archive / Clear buttons
import streamlit as st
from pathlib import Path
from io import BytesIO
import qrcode, csv, json, os, time, urllib.parse, hashlib, uuid, shutil
from datetime import datetime
import pandas as pd

# ---------------- CONFIG ----------------
ENFORCE_CID = True   # set False if you don't want device-lock (cid)
SLOT_TTL = 600      # 10 minutes in seconds
st.set_page_config(page_title="QR Attendance", layout="wide")
# ---------------- SECRETS ----------------
QR_SECRET = st.secrets.get("QR_SECRET", "changeme")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")
BASE_URL = st.secrets.get("BASE_URL", "https://qr-attendance.streamlit.app")  # must match app host
# ---------------- PATHS ----------------
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "attendance.csv"
SLOT_FILE = DATA_DIR / "current_slot.json"
ARCHIVE_DIR = DATA_DIR / "archive"; ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------- HELPERS ----------------
def now_iso_utc(): return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
def fmt_local(isoz):
    try:
        dt = datetime.fromisoformat(str(isoz).replace("Z",""))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except: return str(isoz)
def atomic_write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(data,f); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)
def read_json_safe(path: Path):
    if not path.exists(): return None
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except: return None

def ensure_slot(ttl=SLOT_TTL):
    now_ts = int(time.time())
    data = read_json_safe(SLOT_FILE)
    if data and isinstance(data, dict):
        slot = data.get("slot_key"); created = int(data.get("created",0))
        if slot and (now_ts - created) <= ttl:
            return slot, created
    new_slot = uuid.uuid4().hex
    payload = {"slot_key": new_slot, "created": now_ts}
    try:
        atomic_write_json(SLOT_FILE, payload)
    except:
        try:
            with open(SLOT_FILE,"w",encoding="utf-8") as f: json.dump(payload,f)
        except: pass
    return new_slot, now_ts

def build_link(slot_key, cid=None):
    q = {"key": slot_key, "s": QR_SECRET}
    if cid: q["cid"] = cid
    return f"{BASE_URL}/?{urllib.parse.urlencode(q)}"

def make_qr_bytes(link):
    img = qrcode.make(link); b = BytesIO(); img.save(b,format="PNG"); b.seek(0); return b

def safe_append_csv(row: dict):
    try:
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        exists = CSV_PATH.exists()
        with open(CSV_PATH,"a",newline="",encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not exists:
                writer.writeheader(); f.flush(); os.fsync(f.fileno())
            writer.writerow(row); f.flush(); os.fsync(f.fileno())
        return True, ""
    except Exception as e:
        return False, str(e)

def read_df():
    if not CSV_PATH.exists():
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH,index=False)
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])
    try:
        return pd.read_csv(CSV_PATH)
    except:
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])

def df_for_export(df):
    if df.empty: return df
    d = df.copy()
    if "timestamp" in d.columns:
        d["timestamp"] = d["timestamp"].apply(fmt_local)
    cols = [c for c in ["timestamp","slot_key","name","email"] if c in d.columns]
    return d.loc[:, cols]

def df_to_xlsx_bytes(df):
    bio = BytesIO()
    d = df_for_export(df)
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        d.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0); return bio.getvalue()

def archive_csv():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S"); dest = ARCHIVE_DIR / f"att_{ts}.csv"
    try:
        if CSV_PATH.exists(): shutil.move(str(CSV_PATH), str(dest))
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH,index=False)
        return True, str(dest)
    except Exception as e: return False, str(e)

def clear_csv():
    try:
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH,index=False)
        return True, ""
    except Exception as e: return False, str(e)

# ---------------- SLOT + UI skeleton ----------------
slot_key, slot_created = ensure_slot()
expires_in = int(SLOT_TTL - (time.time() - slot_created))
canonical_link = build_link(slot_key)  # QR contains canonical (no cid)

st.markdown("<style> .big-btn{padding:12px 16px;border-radius:8px;} </style>", unsafe_allow_html=True)
st.title("📋 QR Attendance Marker")

left, right = st.columns([2,1])
with right:
    st.subheader("Admin — Current QR")
    st.write("Slot key:", f"`{slot_key}`")
    st.write(f"Refreshes in **{expires_in}s**")
    st.image(make_qr_bytes(canonical_link), width=220)
    st.markdown("**Open / copy below attach your browser CID automatically.**")
    # admin buttons (open with cid, copy with cid) - visible and usable on mobile & desktop
    js_admin = f"""
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <button id="openWithCid" class="big-btn" style="background:#2b6cb0;color:white;border:none;">Open in new tab (with cid)</button>
      <button id="copyWithCid" class="big-btn" style="background:#4a5568;color:white;border:none;">Copy link (with cid)</button>
    </div>
    <script>
      function getCidLocal(){ try{ let c=localStorage.getItem('attendance_cid'); if(!c){ c=(crypto && crypto.randomUUID)?crypto.randomUUID(): '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', c);} return c; }catch(e){ return '{uuid.uuid4().hex}'; } }
      document.getElementById('openWithCid').onclick = function(){ const cid = encodeURIComponent(getCidLocal()); window.open("{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="+cid, "_blank"); }
      document.getElementById('copyWithCid').onclick = async function(){ try{ const cid = encodeURIComponent(getCidLocal()); const url = "{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="+cid; await navigator.clipboard.writeText(url); this.innerText='Copied'; setTimeout(()=>this.innerText='Copy link (with cid)',1200);}catch(e){alert('Copy failed')} }
    </script>
    """
    st.components.v1.html(js_admin, height=90)

with left:
    st.subheader("Open with CID (auto)")
    st.write("Scan QR — the page will try to auto-create a CID and reload with it. If that fails you'll see a big fallback button.")
    st.markdown("---")
    st.markdown("### Mark Attendance")

# ---------------- QUERY PARAMS + AUTO-CID + FALLBACK ----------------
params = st.experimental_get_query_params()
if "key" in params and "s" in params:
    s_ok = params.get("s", [""])[0] == QR_SECRET
    key_ok = params.get("key", [""])[0] == slot_key
    if s_ok and key_ok and ("cid" not in params):
        # attempt auto-inject + redirect
        js_auto = f"""
        <div style="padding:12px;border-radius:8px;background:#1f2937;color:#fff;">
          <script>
            (function(){{
              try {{
                let cid = localStorage.getItem('attendance_cid');
                if(!cid){ cid = (crypto && crypto.randomUUID)?crypto.randomUUID(): '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', cid); }
                const base = window.location.origin + window.location.pathname;
                const p = new URLSearchParams(window.location.search);
                p.set('cid', cid);
                window.location.replace(base + '?' + p.toString());
              }} catch(e) {{
                // auto failed -> show big fallback button below (rendered by Streamlit)
                console.error('auto-cid failed', e);
              }}
            }})();
          </script>
          <div style="margin-top:10px;">
            <strong>If nothing happened, click the fallback button below:</strong>
            <div style="margin-top:10px;">
              <button id="fallbackCid" class="big-btn" style="background:#e53e3e;color:white;border:none;">Enable CID & Continue</button>
            </div>
          </div>
        </div>
        <script>
          document.addEventListener('DOMContentLoaded', function(){
            document.getElementById('fallbackCid').onclick = function(){ try {
              let cid = localStorage.getItem('attendance_cid');
              if(!cid){ cid = (crypto && crypto.randomUUID)?crypto.randomUUID(): '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', cid); }
              const base = window.location.origin + window.location.pathname;
              const p = new URLSearchParams(window.location.search);
              p.set('cid', cid);
              window.location.replace(base + '?' + p.toString());
            } catch(e){ alert('This viewer blocks required features. Open link in your phone browser (Chrome/Firefox).'); } };
          });
        </script>
        """
        st.components.v1.html(js_auto, height=220)
        st.stop()

# refresh params (in case redirect occurred)
params = st.experimental_get_query_params()
valid_link = False; cid = None
if "key" in params and "s" in params and (not ENFORCE_CID or "cid" in params):
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        cid = params.get("cid", [None])[0] if "cid" in params else None
        if not ENFORCE_CID or (cid and len(str(cid))>8):
            valid_link = True

# ---------------- FORM ----------------
with st.form("attendance"):
    name = st.text_input("Full name")
    email = st.text_input("Email")
    submitted = st.form_submit_button("Submit")

if submitted:
    if not name.strip() or not email.strip():
        st.error("Fill both fields.")
    elif not valid_link:
        st.error("Submission blocked: page missing valid CID. Use 'Open in new tab (with cid)' or press fallback button (if shown) and try again.")
    else:
        df = read_df()
        duplicate = False
        if ENFORCE_CID and cid:
            try:
                duplicate = ((df['slot_key'] == slot_key) & (df.get('cid','') == cid)).any()
            except: duplicate = False
        if duplicate:
            st.error("This device already submitted for this slot.")
        else:
            row = {"timestamp": now_iso_utc(), "slot_key": slot_key, "name": name.strip(), "email": email.strip(), "cid": cid or ""}
            ok, err = safe_append_csv(row)
            if ok: st.success("Attendance saved.")
            else: st.error("Save failed: " + str(err))

# ---------------- ADMIN PANEL ----------------
st.markdown("---")
with st.expander("Admin — View / Archive / Clear (password required)"):
    pw = st.text_input("Password", type="password")
    if st.button("Show records"):
        if pw == ADMIN_PASSWORD:
            df = read_df()
            view = df_for_export(df)
            if view.empty: st.info("No entries.")
            else:
                st.dataframe(view)
                st.download_button("Download CSV", data=view.to_csv(index=False).encode("utf-8"), file_name="attendance.csv")
                try:
                    st.download_button("Download Excel (.xlsx)", data=df_to_xlsx_bytes(df), file_name="attendance.xlsx")
                except Exception as e:
                    st.error("Excel export failed."); st.text(str(e))
        else:
            st.error("Wrong password.")

    st.markdown("### Archive / Clear")
    arch_tok = st.text_input("Type ARCHIVE to confirm", key="arch")
    if st.button("Archive"):
        if pw != ADMIN_PASSWORD: st.error("Enter admin password first.")
        elif arch_tok != "ARCHIVE": st.warning("Type ARCHIVE to confirm.")
        else:
            ok, info = archive_csv()
            if ok: st.success(f"Archived: {info}")
            else: st.error("Archive failed: "+str(info))

    clr_tok = st.text_input("Type CLEAR to confirm", key="clr")
    if st.button("Clear now"):
        if pw != ADMIN_PASSWORD: st.error("Enter admin password first.")
        elif clr_tok != "CLEAR": st.warning("Type CLEAR to confirm.")
        else:
            ok, info = clear_csv()
            if ok: st.success("Cleared current records.")
            else: st.error("Clear failed: "+str(info))

st.caption(f"ENFORCE_CID={ENFORCE_CID} • Slot TTL = {SLOT_TTL}s (10 minutes = 600s). Exports exclude cid; cid stored for enforcement only.")
