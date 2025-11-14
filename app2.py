# app2.py - Auto-CID + visible fallback button + improved desktop/mobile layout
import streamlit as st
from pathlib import Path
from io import BytesIO
import qrcode, csv, json, os, time, urllib.parse, hashlib, uuid, shutil
from datetime import datetime
import pandas as pd

# ---------- page config ----------
st.set_page_config(page_title="QR Attendance", layout="wide")
try:
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    sha = "no-sha"
st.sidebar.text(f"app2.py SHA: {sha}")

# ---------- secrets / config ----------
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

def local_fmt(iso_z):
    try:
        dt = datetime.fromisoformat(iso_z.replace("Z", ""))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_z

def atomic_write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

def read_json_safe(path: Path):
    if not path.exists(): return None
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except Exception:
        return None

def ensure_slot(ttl=SLOT_TTL):
    now_ts = int(time.time())
    data = read_json_safe(SLOT_FILE)
    if data and isinstance(data, dict):
        slot = data.get("slot_key")
        created = int(data.get("created",0))
        if slot and (now_ts - created) <= ttl:
            return slot, created
    new = uuid.uuid4().hex
    payload = {"slot_key": new, "created": now_ts}
    try:
        atomic_write_json(SLOT_FILE, payload)
    except Exception:
        try:
            with open(SLOT_FILE,"w",encoding="utf-8") as f: json.dump(payload,f)
        except Exception:
            pass
    return new, now_ts

def build_link(slot_key, cid=None):
    p = {"key": slot_key, "s": QR_SECRET}
    if cid: p["cid"] = cid
    return f"{BASE_URL}/?{urllib.parse.urlencode(p)}"

def make_qr_bytes(link):
    img = qrcode.make(link)
    b = BytesIO(); img.save(b, format="PNG"); b.seek(0); return b

def safe_append_csv(row):
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
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH,index=False)
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])
    try:
        return pd.read_csv(CSV_PATH)
    except Exception:
        return pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"])

def df_for_export(df):
    if df.empty: return df
    df2 = df.copy()
    if "timestamp" in df2.columns:
        df2["timestamp"] = df2["timestamp"].apply(local_fmt)
    cols = [c for c in ["timestamp","slot_key","name","email"] if c in df2.columns]
    return df2.loc[:, cols]

def df_to_xlsx_bytes(df):
    df2 = df_for_export(df)
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df2.to_excel(writer, index=False, sheet_name="attendance")
    bio.seek(0); return bio.getvalue()

def archive_csv():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"att_{ts}.csv"
    try:
        if CSV_PATH.exists(): shutil.move(str(CSV_PATH), str(dest))
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH,index=False)
        return True, str(dest)
    except Exception as e:
        return False, str(e)

def clear_csv():
    try:
        pd.DataFrame(columns=["timestamp","slot_key","name","email","cid"]).to_csv(CSV_PATH,index=False)
        return True, ""
    except Exception as e:
        return False, str(e)

# ---------- slot ----------
slot_key, slot_created = ensure_slot(SLOT_TTL)
expires = int(SLOT_TTL - (time.time() - slot_created))
canonical = build_link(slot_key)  # QR contains canonical (no cid)

# ---------- simple CSS to improve desktop layout ----------
st.markdown("""
<style>
/* make input fields taller and buttons bigger */
.stTextInput>div>div>input, .stTextInput>div>div>textarea { padding:14px; font-size:16px; }
.stButton>button { padding:10px 16px; font-size:15px; }
section.main>div { max-width:1600px; margin:0 auto; } /* center and widen */
@media (min-width: 1000px) {
  .big-left { width: 68%; display:inline-block; vertical-align:top; padding-right:18px; }
  .big-right { width: 30%; display:inline-block; vertical-align:top; }
}
@media (max-width: 999px) {
  .big-left, .big-right { width:100%; display:block; }
}
.card { border-radius:10px; padding:18px; border:1px solid rgba(255,255,255,0.03); background:transparent; }
</style>
""", unsafe_allow_html=True)

# ---------- header ----------
st.title("📋 QR Attendance — improved UI")
col_left, col_right = st.columns([2,1])

with col_left:
    st.markdown("<div class='card big-left'>", unsafe_allow_html=True)
    st.subheader("Mark Attendance")
    st.write("Scan the QR or use the buttons to open the form. The app will attempt to auto-create a device id (CID). If auto-inject fails a big fallback button is shown — click it once and you'll be redirected with CID appended.")
    st.markdown("</div>", unsafe_allow_html=True)

with col_right:
    st.markdown("<div class='card big-right'>", unsafe_allow_html=True)
    st.subheader("Admin — Current QR")
    st.write("Slot key:", f"`{slot_key}`")
    st.write(f"Refresh in **{expires}s**")
    st.image(make_qr_bytes(canonical), width=220)
    st.markdown("<small>Buttons below will attach your browser's CID and open the form.</small>", unsafe_allow_html=True)
    # admin buttons (open-with-cid, copy-with-cid)
    js = f"""
    <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
      <button id="openCid" style="padding:8px 12px;background:#2b6cb0;color:white;border:none;border-radius:8px;">Open in new tab (with cid)</button>
      <button id="copyCid" style="padding:8px 12px;background:#4a5568;color:white;border:none;border-radius:8px;">Copy (with cid)</button>
    </div>
    <script>
    function getCid(){{ try{{ let c=localStorage.getItem('attendance_cid'); if(!c){ c=(crypto && crypto.randomUUID)?crypto.randomUUID() : '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', c);} return c; }}catch(e){ return '{uuid.uuid4().hex}'; } }}
    document.getElementById('openCid').onclick = function(){{ const cid = encodeURIComponent(getCid()); window.open("{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="+cid, "_blank"); }};
    document.getElementById('copyCid').onclick = async function(){{ try{{ const cid = encodeURIComponent(getCid()); const url = "{BASE_URL}/?key={slot_key}&s={QR_SECRET}&cid="+cid; await navigator.clipboard.writeText(url); this.innerText='Copied'; setTimeout(()=>this.innerText='Copy (with cid)',1200);}}catch(e){alert('Copy failed')}}};
    </script>
    """
    st.components.v1.html(js, height=90)
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ---------- Query params & auto-cid injection ----------
params = st.experimental_get_query_params()

# If key+s valid but no cid -> attempt auto-inject via JS and redirect.
if "key" in params and "s" in params:
    s_ok = params.get("s", [""])[0] == QR_SECRET
    key_ok = params.get("key", [""])[0] == slot_key
    if s_ok and key_ok and "cid" not in params:
        # Auto-redirect JS (runs in browser). Also render a visible fallback button that runs same JS.
        js_auto = f"""
        <div style="margin:12px 0;">
          <script>
          (function(){{
            try {{
              // attempt auto-cid and redirect
              let cid = localStorage.getItem('attendance_cid');
              if(!cid){{ cid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', cid); }}
              const base = window.location.origin + window.location.pathname;
              const params = new URLSearchParams(window.location.search);
              params.set('cid', cid);
              // replace so back-button not filled with both versions
              window.location.replace(base + '?' + params.toString());
            }} catch(e) {{
              // If auto-redirect blocked, do nothing here; fallback button below will help.
              console.error('auto-cid failed', e);
            }}
          }})();
          </script>
          <div style="padding:14px;border-radius:8px;background:#2b2b2b;color:#fff;margin-top:8px;">
            <strong>Auto-CID failed or blocked.</strong><br>
            If you see this message, click the big button below to enable CID and continue.
            <div style="margin-top:10px;">
              <button id="fallbackCid" style="padding:12px 16px;background:#e55353;color:white;border:none;border-radius:8px;">Enable CID & Continue</button>
            </div>
          </div>
          <script>
            document.getElementById('fallbackCid').onclick = function() {{
              try {{
                let cid = localStorage.getItem('attendance_cid');
                if(!cid){{ cid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : '{uuid.uuid4().hex}'; localStorage.setItem('attendance_cid', cid); }}
                const base = window.location.origin + window.location.pathname;
                const params = new URLSearchParams(window.location.search);
                params.set('cid', cid);
                window.location.replace(base + '?' + params.toString());
              }} catch(e) {{
                alert('Failed to set CID in this viewer. Open this link in your browser (Chrome/Firefox) and try again.');
              }}
            }};
          </script>
        </div>
        """
        st.components.v1.html(js_auto, height=220)
        st.stop()

# ---------- validate that we have good key+s+cid ----------
params = st.experimental_get_query_params()  # refresh
valid_link = False; cid = None
if "key" in params and "s" in params and "cid" in params:
    if params.get("s", [""])[0] == QR_SECRET and params.get("key", [""])[0] == slot_key:
        cid = params.get("cid", [None])[0]
        if cid and len(str(cid)) > 8:
            valid_link = True

# ---------- attendance form UI ----------
left_col, right_col = st.columns([2,1])
with left_col:
    with st.form("attendance_form"):
        name = st.text_input("Full name")
        email = st.text_input("Email")
        submitted = st.form_submit_button("Submit")
    if submitted:
        if not name.strip() or not email.strip():
            st.error("Please fill name and email.")
        elif not valid_link:
            st.error("Invalid or missing CID. (Auto-CID failed. Try scanning QR again or use the 'Open in new tab (with cid)' button.)")
        else:
            df = read_df()
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
                    st.success("Attendance recorded. Thanks!")
                else:
                    st.error("Save failed.")
                    st.text(err)

with right_col:
    st.write("Admin actions")
    with st.expander("Admin — View / Download / Manage"):
        pw = st.text_input("Password", type="password")
        if st.button("Show records"):
            if pw == ADMIN_PASSWORD:
                df = read_df()
                df_display = df_for_export(df)
                if df_display.empty:
                    st.info("No records.")
                else:
                    st.dataframe(df_display)
                    st.download_button("CSV", data=df_display.to_csv(index=False).encode("utf-8"), file_name="attendance.csv")
                    try:
                        st.download_button("XLSX", data=df_to_xlsx_bytes(df), file_name="attendance.xlsx")
                    except Exception as e:
                        st.error("Excel failed"); st.text(str(e))
            else:
                st.error("Wrong password.")
        st.markdown("---")
        st.write("Archive current records (keeps backup)")
        archive_token = st.text_input("Type ARCHIVE to confirm", key="arch")
        if st.button("Archive now"):
            if pw != ADMIN_PASSWORD:
                st.error("Enter admin password first.")
            elif archive_token != "ARCHIVE":
                st.warning("Type ARCHIVE exactly to confirm.")
            else:
                ok, info = archive_csv()
                if ok: st.success(f"Archived: {info}")
                else: st.error(f"Archive failed: {info}")

        st.write("Clear current records (start fresh)")
        clear_token = st.text_input("Type CLEAR to confirm", key="clr")
        if st.button("Clear now"):
            if pw != ADMIN_PASSWORD:
                st.error("Enter admin password first.")
            elif clear_token != "CLEAR":
                st.warning("Type CLEAR exactly to confirm.")
            else:
                ok, info = clear_csv()
                if ok: st.success("Cleared.")
                else: st.error(f"Clear failed: {info}")

st.caption("One submission per device per slot enforced. Exports exclude cid. If QR scanning opens an app viewer that blocks JS, use 'Open in new tab (with cid)' or copy-with-cid and open in a full browser.")
