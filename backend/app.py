import os
import json
import re
import base64
import uuid
import socket
import threading
import time
 
import requests
import streamlit as st
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from groq import Groq
from database import init_db, save_user, save_wardrobe_item, get_wardrobe_for_user, get_connection
 
load_dotenv()
 
BACKEND_PORT = 3000
BACKEND_URL = f"http://localhost:{BACKEND_PORT}"
 
#BACKEND (Flask)

 
backend_app = Flask(__name__)
CORS(backend_app)
 
client = Groq(api_key=os.environ['GROQ_API_KEY'])
VISION_MODEL = "qwen/qwen3.6-27b"
TEXT_MODEL = "openai/gpt-oss-120b"
 
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
 
init_db()
 
 
def to_image_url(file_storage):
    file_bytes = file_storage.read()
    b64 = base64.b64encode(file_bytes).decode('utf-8')
    return f"data:{file_storage.mimetype};base64,{b64}"
 
 
def safe_parse(text):
    text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
    cleaned = text.replace('```json', '').replace('```', '').strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {'error': 'Could not parse response', 'raw': text}
 
 
def get_weather(location):
    """Free, no-signup weather lookup using wttr.in. Returns a short description string, or None if it fails."""
    try:
        resp = requests.get(f"https://wttr.in/{location}?format=j1", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        current = data['current_condition'][0]
        temp_c = current['temp_C']
        desc = current['weatherDesc'][0]['value']
        return f"{temp_c}°C and {desc}"
    except Exception as e:
        print('Weather lookup failed:', e)
        return None
 
 
def parse_availability(raw_value):
    """Handles availability sent as 'true'/'false' strings, '1'/'0', or actual booleans from the frontend."""
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return True  # default to available if not specified
    return str(raw_value).strip().lower() in ('true', '1', 'yes', 'on')
 
 
@backend_app.route('/api/scan-body', methods=['POST'])
def scan_body():
    try:
        files = request.files.getlist('photo')
        name = request.form.get('name', 'Guest')
 
        if not files:
            return jsonify({'error': 'No photo uploaded'}), 400
 
        image_urls = [to_image_url(f) for f in files]
 
        prompt = """You are a professional fashion stylist and color analyst.
Look at these photos and estimate:
1. Body shape category (pear, hourglass, rectangle, apple, or inverted triangle)
2. Skin tone (e.g. fair, olive, deep, etc)
3. General proportions (e.g. "shorter torso, longer legs")
Respond ONLY with valid JSON, no other text, in this exact format:
{"shape": "...", "skintone": "...", "proportions": "..."}"""
 
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": url}} for url in image_urls
        ]
 
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=1500,
            reasoning_effort="none",
            response_format={"type": "json_object"},
        )
        result = safe_parse(response.choices[0].message.content)
 
        if 'error' in result:
            return jsonify(result)
 
        bodytype = f"{result.get('shape')} ({result.get('proportions')})"
        skincolor = result.get('skintone')
 
        user_id = save_user(name, bodytype, skincolor)
 
        return jsonify({
            'user_id': user_id,
            'shape': result.get('shape'),
            'skintone': result.get('skintone'),
            'proportions': result.get('proportions')
        })
 
    except Exception as e:
        print('Error in /api/scan-body:', e)
        return jsonify({'error': 'Something went wrong scanning the photo.'}), 500
 
 
@backend_app.route('/api/scan-item', methods=['POST'])
def scan_item():
    try:
        file = request.files.get('photo')
        user_id = request.form.get('user_id')
        manual_type = request.form.get('type')  # optional user-provided override
        availability = parse_availability(request.form.get('availability'))
 
        if not file:
            return jsonify({'error': 'No photo uploaded'}), 400
        if not user_id:
            return jsonify({'error': 'Missing user_id — scan a body first to create a user.'}), 400
 
        file_bytes = file.read()
        file.seek(0)
 
        ext = file.mimetype.split('/')[-1]
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        with open(filepath, 'wb') as f:
            f.write(file_bytes)
 
        image_url = to_image_url(file)
 
        prompt = """Identify this clothing item. Respond ONLY with valid JSON in this exact format:
{"type": "...", "color": "...", "cut": "...", "fabric": "..."}
"cut" means the silhouette/fit style (e.g. slim, relaxed, oversized, tailored).
Example: {"type": "blazer", "color": "navy", "cut": "tailored", "fabric": "wool"}"""
 
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]
 
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": content}]
        )
        result = safe_parse(response.choices[0].message.content)
 
        if 'error' in result:
            return jsonify(result)
 
        final_type = manual_type if manual_type else result.get('type')
 
        save_wardrobe_item(
            user_id=user_id,
            ctype=final_type,
            color=result.get('color'),
            cut=result.get('cut'),
            size='M',
            fabric=result.get('fabric'),
            availability=availability,
            image_path=filename
        )
 
        result['type'] = final_type
        result['availability'] = availability
        return jsonify(result)
 
    except Exception as e:
        print('Error in /api/scan-item:', e)
        return jsonify({'error': 'Something went wrong scanning the item.'}), 500
 
 
@backend_app.route('/api/wardrobe/<int:user_id>', methods=['GET'])
def wardrobe(user_id):
    try:
        items = get_wardrobe_for_user(user_id)
        safe_items = json.loads(json.dumps(items, default=str))
        return jsonify({'items': safe_items})
    except Exception as e:
        print('Error in /api/wardrobe:', e)
        return jsonify({'error': 'Something went wrong fetching the wardrobe.'}), 500
 
 
@backend_app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)
 
 
@backend_app.route('/api/style-closet', methods=['POST'])
def style_closet():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        location = data.get('location')  # optional, e.g. "Chennai" or "New York"
 
        if not user_id:
            return jsonify({'error': 'Missing user_id'}), 400
 
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if not user:
            return jsonify({'error': 'No user found with that ID.'}), 400
 
        all_items = get_wardrobe_for_user(user_id)
 
        # Only use items marked as available
        wardrobe_items = [item for item in all_items if parse_availability(item.get('availability'))]
 
        if not wardrobe_items:
            return jsonify({'error': 'No available wardrobe items found. Add some items and mark them available first.'}), 400
 
        weather_line = ""
        if location:
            weather = get_weather(location)
            if weather:
                weather_line = f"\nCurrent weather in {location}: {weather}. Factor this into fabric and layering choices."
 
        item_summaries = "\n".join(
            f"id={item['id']}: {item.get('color')} {item.get('cut')} {item.get('type')} ({item.get('fabric')})"
            for item in wardrobe_items
        )
 
        prompt = f"""You are a professional fashion stylist.
This person has: body type = {user['bodytype']}, skintone = {user['skincolor']}.
{weather_line}
 
Their available wardrobe items are listed below, one per line as "id=NUMBER: description":
{item_summaries}
 
Suggest 2-3 outfit combinations using ONLY these items. For each outfit, provide:
- "name": a short outfit name
- "item_ids": a JSON array of the exact id numbers used (integers, copied exactly from the list above — do not invent new ids)
- "reason": a 1-2 sentence explanation of why it flatters this person's body type and coloring, and if weather was provided, why it suits today's conditions
 
Respond ONLY with valid JSON, no other text, in this exact format:
{{"outfits": [{{"name": "...", "item_ids": [1, 2, 3], "reason": "..."}}]}}"""
 
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        result = safe_parse(response.choices[0].message.content)
 
        if 'error' in result:
            return jsonify(result)
 
        items_by_id = {}
        for item in wardrobe_items:
            items_by_id[int(item['id'])] = item
 
        for outfit in result.get('outfits', []):
            resolved_items = []
            for raw_id in outfit.get('item_ids', []):
                try:
                    item_id = int(raw_id)
                except (ValueError, TypeError):
                    continue
                item = items_by_id.get(item_id)
                if item:
                    resolved_items.append(item)
            outfit['items'] = resolved_items
 
        return jsonify(result)
 
    except Exception as e:
        print('Error in /api/style-closet:', e)
        return jsonify({'error': 'Something went wrong generating outfits.'}), 500
 
 
def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0
 
 
def start_backend():
    """Start the Flask backend in a daemon thread, once per process.
    Streamlit reruns this whole script on every interaction, so we
    guard against re-binding the port on each rerun."""
    if _port_in_use(BACKEND_PORT):
        return  
 
    def _run():
        backend_app.run(port=BACKEND_PORT, debug=False, use_reloader=False)
 
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
 
    for _ in range(20):
        if _port_in_use(BACKEND_PORT):
            break
        time.sleep(0.1)
 
 
start_backend()
 

#FRONTEND (Streamlit)

 
st.set_page_config(page_title="FSHN.", page_icon="🪞", layout="wide")
 

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Clash+Display:wght@400;600;700&family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap');
 
    #MainMenu, footer, header {visibility: hidden;}
 
    * { font-family: 'Space Grotesk', sans-serif; }
 
    /* ---- animated aurora background ---- */
    @keyframes drift {
        0%   { background-position: 0% 0%, 100% 0%, 50% 100%, 0 0; }
        50%  { background-position: 30% 20%, 70% 30%, 60% 70%, 0 0; }
        100% { background-position: 0% 0%, 100% 0%, 50% 100%, 0 0; }
    }
    .stApp {
        background:
            radial-gradient(circle at 15% 20%, rgba(168, 85, 247, 0.45), transparent 42%),
            radial-gradient(circle at 85% 10%, rgba(236, 72, 153, 0.40), transparent 42%),
            radial-gradient(circle at 50% 90%, rgba(59, 130, 246, 0.35), transparent 48%),
            radial-gradient(circle at 90% 80%, rgba(250, 204, 21, 0.18), transparent 40%),
            #060608;
        background-size: 200% 200%, 200% 200%, 200% 200%, 100% 100%;
        animation: drift 22s ease-in-out infinite;
    }
 
    section[data-testid="stSidebar"] {
        background: rgba(10, 10, 14, 0.92);
        border-right: 1px solid rgba(255,255,255,0.10);
        box-shadow: 8px 0 40px rgba(168,85,247,0.15);
    }
 
    h1, h2, h3 {
        color: #f5f5f7 !important;
        letter-spacing: -0.02em;
        font-weight: 700;
        text-shadow: 0 0 30px rgba(168,85,247,0.25);
    }
    p, span, label, .stMarkdown { color: #d6d6e0; }
 
    /* ---- glass panels, turned up ---- */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: linear-gradient(160deg, rgba(255,255,255,0.07), rgba(255,255,255,0.02));
        backdrop-filter: blur(20px);
        border: 1px solid rgba(255,255,255,0.14) !important;
        border-radius: 22px !important;
        padding: 6px;
        box-shadow: 0 8px 40px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.08);
    }
 
    /* ---- glow-gradient buttons ---- */
    .stButton > button {
        background: linear-gradient(135deg, #a855f7, #ec4899 55%, #fbbf24);
        background-size: 200% 200%;
        color: white;
        border: none;
        border-radius: 999px;
        padding: 0.6em 1.9em;
        font-weight: 700;
        letter-spacing: 0.02em;
        box-shadow: 0 6px 24px rgba(236,72,153,0.45), 0 0 0 1px rgba(255,255,255,0.08) inset;
        transition: transform 0.18s ease, box-shadow 0.18s ease, background-position 0.4s ease;
    }
    .stButton > button:hover {
        transform: scale(1.05) translateY(-2px);
        background-position: 100% 50%;
        box-shadow: 0 10px 34px rgba(236,72,153,0.6), 0 0 0 1px rgba(255,255,255,0.14) inset;
    }
 
    .stTextInput input {
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(255,255,255,0.14) !important;
        border-radius: 12px !important;
        color: #f5f5f7 !important;
    }
    .stTextInput input:focus { border-color: #ec4899 !important; box-shadow: 0 0 0 3px rgba(236,72,153,0.25) !important; }
 
    section[data-testid="stSidebar"] .stRadio label { font-weight: 600; letter-spacing: 0.01em; }
 
    /* ---- garment / outfit cards ---- */
    .glass-row { display: flex; gap: 20px; margin-bottom: 24px; flex-wrap: wrap; }
    .glass-card {
        background: linear-gradient(160deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
        backdrop-filter: blur(18px);
        border: 1px solid rgba(255,255,255,0.14);
        border-radius: 22px;
        padding: 14px;
        width: 180px;
        text-align: center;
        box-shadow: 0 10px 34px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.04) inset;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .glass-card:hover {
        transform: translateY(-4px) scale(1.02);
        box-shadow: 0 16px 44px rgba(168,85,247,0.35), 0 0 0 1px rgba(255,255,255,0.08) inset;
    }
    .glass-card img {
        width: 100%; height: 140px; object-fit: cover; border-radius: 16px; margin-bottom: 8px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    }
    .glass-card .tag { font-size: 0.78rem; color: #e4e4ee; font-style: italic; }
    .stagger-1 { margin-top: 0px; }
    .stagger-2 { margin-top: 34px; }
    .stagger-3 { margin-top: 12px; }
 
    /* ---- hero: bigger, glowier, badge spins faster & brighter ---- */
    .hero-wrap {
        position: relative;
        display: flex; align-items: center; justify-content: center;
        height: 480px;
        margin-bottom: 6px;
    }
    .hero-glow {
        position: absolute;
        width: 420px; height: 420px;
        background: radial-gradient(circle, rgba(168,85,247,0.55), rgba(236,72,153,0.25) 45%, transparent 70%);
        filter: blur(10px);
        animation: pulseglow 4.5s ease-in-out infinite;
    }
    @keyframes pulseglow {
        0%, 100% { opacity: 0.65; transform: scale(1); }
        50% { opacity: 1; transform: scale(1.08); }
    }
    .wordmark {
        position: absolute;
        font-family: 'Clash Display', 'Space Grotesk', sans-serif;
        font-size: 5.2rem;
        font-weight: 700;
        background: linear-gradient(135deg, #ffffff, #d8b4fe 40%, #f9a8d4 70%, #fde68a);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        letter-spacing: -0.02em;
        filter: drop-shadow(0 0 26px rgba(216,180,254,0.55));
        z-index: 2;
    }
    .badge-spin {
        width: 320px;
        height: 320px;
        animation: spin 14s linear infinite;
        filter: drop-shadow(0 0 14px rgba(236,72,153,0.45));
        z-index: 1;
    }
    .badge-spin-inner {
        width: 250px;
        height: 250px;
        animation: spinrev 10s linear infinite;
        position: absolute;
        opacity: 0.7;
        z-index: 1;
    }
    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    @keyframes spinrev { from { transform: rotate(360deg); } to { transform: rotate(0deg); } }
    .badge-text {
        font-size: 13px;
        letter-spacing: 4px;
        fill: rgba(255,255,255,0.75);
        text-transform: uppercase;
        font-family: 'Space Mono', monospace;
    }
    .badge-text-inner {
        font-size: 10px;
        letter-spacing: 3px;
        fill: rgba(236,72,153,0.85);
        text-transform: uppercase;
        font-family: 'Space Mono', monospace;
    }
    .tagline {
        text-align: center;
        color: #b8b8c8;
        font-family: 'Space Mono', monospace;
        font-size: 0.95rem;
        letter-spacing: 0.06em;
        margin-top: -6px;
        text-transform: uppercase;
    }
</style>
""", unsafe_allow_html=True)
 

#SESSION STATE
defaults = {
    'user_id': None,
    'body_scan': None,
    'name': '',
    'edits': {},
    'outfits': None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v
 
 
def fetch_wardrobe():
    if not st.session_state.user_id:
        return []
    try:
        r = requests.get(f"{BACKEND_URL}/api/wardrobe/{st.session_state.user_id}")
        return r.json().get('items', [])
    except requests.exceptions.ConnectionError:
        return []
    except Exception:
        return []
 
 
def render_glass_row(items, stagger_classes):
    if not items:
        return
    html = '<div class="glass-row">'
    for item, stagger in zip(items, stagger_classes):
        img_url = f"{BACKEND_URL}/uploads/{item.get('image_path')}" if item.get('image_path') else ""
        label = f"{item.get('color', '')} {item.get('cut', '')} {item.get('type', '')}".strip()
        html += f'''
        <div class="glass-card {stagger}">
            {"<img src='" + img_url + "'/>" if img_url else ""}
            <div class="tag">{label}</div>
        </div>'''
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)
 
 

#SIDEBAR NAV

st.sidebar.markdown("### 🪞 FSHN.")
if st.session_state.user_id:
    st.sidebar.caption(f"Signed in as **{st.session_state.name}**")
page = st.sidebar.radio("Navigate", ["Home", "My Wardrobe", "Style My Closet"], label_visibility="collapsed")
 

#HOME

if page == "Home":
    st.markdown("""
    <div class="hero-wrap">
        <div class="hero-glow"></div>
        <svg class="badge-spin" viewBox="0 0 200 200" style="position:absolute;">
            <defs>
                <path id="circlePath" d="M 100,100 m -80,0 a 80,80 0 1,1 160,0 a 80,80 0 1,1 -160,0" />
            </defs>
            <circle cx="100" cy="100" r="80" fill="none" stroke="url(#ringGrad)" stroke-width="1.5" opacity="0.5"/>
            <defs>
                <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stop-color="#a855f7"/>
                    <stop offset="50%" stop-color="#ec4899"/>
                    <stop offset="100%" stop-color="#fbbf24"/>
                </linearGradient>
            </defs>
            <text class="badge-text">
                <textPath href="#circlePath" startOffset="0%">
                    CURATER &#8226; MY WARDROBE &#8226; CURATER &#8226; MY WARDROBE &#8226;
                </textPath>
            </text>
        </svg>
        <svg class="badge-spin-inner" viewBox="0 0 200 200" style="position:absolute;">
            <defs>
                <path id="circlePath2" d="M 100,100 m -65,0 a 65,65 0 1,1 130,0 a 65,65 0 1,1 -130,0" />
            </defs>
            <circle cx="100" cy="100" r="65" fill="none" stroke="rgba(236,72,153,0.4)" stroke-width="1" stroke-dasharray="2 6"/>
            <text class="badge-text-inner">
                <textPath href="#circlePath2" startOffset="0%">
                    SCAN &#8226; STYLE &#8226; REPEAT &#8226; SCAN &#8226; STYLE &#8226; REPEAT &#8226;
                </textPath>
            </text>
        </svg>
        <div class="wordmark">FSHN.</div>
    </div>
    <p class="tagline">Scan your style. Style your closet. Dress for the weather.</p>
    """, unsafe_allow_html=True)
 
    st.divider()
 
    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("Get Started")
        name = st.text_input("Your name", value=st.session_state.name)
        body_photos = st.file_uploader(
            "Upload 1-2 photos of yourself",
            type=['jpg', 'jpeg', 'png'],
            accept_multiple_files=True,
            key="body_uploader"
        )
        if st.button("Scan My Style"):
            if not name:
                st.warning("Please enter your name first.")
            elif not body_photos:
                st.warning("Please upload at least one photo first.")
            else:
                with st.spinner("Scanning..."):
                    try:
                        files = [("photo", (p.name, p.getvalue(), p.type)) for p in body_photos]
                        data = {"name": name}
                        r = requests.post(f"{BACKEND_URL}/api/scan-body", files=files, data=data)
                        result = r.json()
                        if 'error' in result:
                            st.error(result['error'])
                        else:
                            st.session_state.body_scan = result
                            st.session_state.user_id = result.get('user_id')
                            st.session_state.name = name
                            st.rerun()
                    except requests.exceptions.ConnectionError:
                        st.error("Couldn't reach the backend. Is it running?")
                    except Exception as e:
                        st.error(f"Something went wrong: {e}")
 
    with col2:
        st.subheader("Your Profile")
        if st.session_state.body_scan:
            d = st.session_state.body_scan
            with st.container(border=True):
                st.write(f"**Shape:** {d.get('shape')}")
                st.write(f"**Skin tone:** {d.get('skintone')}")
                st.write(f"**Proportions:** {d.get('proportions')}")
        else:
            st.caption("Scan your style to see your profile here.")
 

#MY WARDROBE

elif page == "My Wardrobe":
    st.markdown("## My Wardrobe")
 
    if not st.session_state.user_id:
        st.warning("Scan your style on the Home page first.")
    else:
        with st.container(border=True):
            st.write("**Add new items**")
            uploads = st.file_uploader(
                "Upload clothing photos",
                type=['jpg', 'jpeg', 'png'],
                accept_multiple_files=True,
                key="wardrobe_uploader"
            )
            colA, colB = st.columns([2, 1])
            with colA:
                manual_type = st.text_input("Type override (optional, applies to all uploaded)", key="batch_type")
            with colB:
                is_available = st.checkbox("Available", value=True, key="batch_availability")
 
            if st.button("Add to Wardrobe"):
                if not uploads:
                    st.warning("Upload at least one photo first.")
                else:
                    progress = st.progress(0.0)
                    for i, photo in enumerate(uploads):
                        try:
                            files = {"photo": (photo.name, photo.getvalue(), photo.type)}
                            data = {
                                "user_id": st.session_state.user_id,
                                "type": manual_type,
                                "availability": str(is_available)
                            }
                            requests.post(f"{BACKEND_URL}/api/scan-item", files=files, data=data)
                        except Exception:
                            pass
                        progress.progress((i + 1) / len(uploads))
                    st.success(f"Added {len(uploads)} item(s).")
                    st.rerun()
 
        st.write("")
        items = fetch_wardrobe()
 
        if not items:
            st.caption("No items yet — upload something above.")
        else:
            cols = st.columns(3)
            for i, item in enumerate(items):
                item_id = item.get('id')
                if item_id not in st.session_state.edits:
                    st.session_state.edits[item_id] = {
                        'type': item.get('type', ''),
                        'color': item.get('color', ''),
                        'fabric': item.get('fabric', '')
                    }
                with cols[i % 3]:
                    with st.container(border=True):
                        if item.get('image_path'):
                            st.image(f"{BACKEND_URL}/uploads/{item['image_path']}", use_container_width=True)
                        st.session_state.edits[item_id]['type'] = st.text_input(
                            "Type", value=st.session_state.edits[item_id]['type'], key=f"type_{item_id}"
                        )
                        st.session_state.edits[item_id]['color'] = st.text_input(
                            "Colour", value=st.session_state.edits[item_id]['color'], key=f"color_{item_id}"
                        )
                        st.session_state.edits[item_id]['fabric'] = st.text_input(
                            "Fabric", value=st.session_state.edits[item_id]['fabric'], key=f"fabric_{item_id}"
                        )
                        avail = item.get('availability')
                        st.caption("🟢 Available" if avail else "⚪ Not available")
            st.caption("Edits above are local to this session for quick preview — they aren't saved back to the database yet.")
 

#STYLE MY CLOSET

else:
    st.markdown("## Style My Closet")
 
    if not st.session_state.user_id:
        st.warning("Scan your style on the Home page first.")
    else:
        col1, col2 = st.columns([2, 1])
        with col1:
            location = st.text_input("Your city (optional — factors in today's weather)", key="location_input")
        with col2:
            st.write("")
            st.write("")
            generate = st.button("Generate Outfits")
 
        if generate:
            with st.spinner("Styling..."):
                try:
                    payload = {"user_id": st.session_state.user_id}
                    if location:
                        payload["location"] = location
                    r = requests.post(f"{BACKEND_URL}/api/style-closet", json=payload)
                    result = r.json()
                    if 'error' in result:
                        st.error(result['error'])
                        st.session_state.outfits = None
                    else:
                        st.session_state.outfits = result.get('outfits', [])
                except requests.exceptions.ConnectionError:
                    st.error("Couldn't reach the backend.")
                except Exception as e:
                    st.error(f"Something went wrong: {e}")
 
        st.write("")
 
        if st.session_state.outfits:
            stagger_cycle = ["stagger-1", "stagger-2", "stagger-3"]
            for outfit in st.session_state.outfits:
                st.markdown(f"#### {outfit.get('name')}")
                render_glass_row(outfit.get('items', []), stagger_cycle)
                st.caption(outfit.get('reason', ''))
        else:
            items = fetch_wardrobe()
            if items:
                st.caption("Your wardrobe — generate outfits above to see full looks.")
                stagger_cycle = ["stagger-1", "stagger-2", "stagger-3"]
                chunk_size = 3
                for i in range(0, len(items), chunk_size):
                    render_glass_row(items[i:i + chunk_size], stagger_cycle)
            else:
                st.caption("Add items to My Wardrobe first, then come back here.")
