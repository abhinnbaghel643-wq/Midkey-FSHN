import os
import json
import re
import base64
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from groq import Groq
from database import init_db, save_user, save_wardrobe_item, get_wardrobe_for_user, get_connection

load_dotenv()

app = Flask(__name__)
CORS(app)

client = Groq(api_key=os.environ['GROQ_API_KEY'])
VISION_MODEL = "qwen/qwen3.6-27b"
TEXT_MODEL = "openai/gpt-oss-120b"

import uuid

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

init_db()


def to_image_url(file_storage):
    """Groq needs images as base64 data URLs, not raw bytes like Gemini did."""
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


@app.route('/api/scan-body', methods=['POST'])
def scan_body():
    try:
        files = request.files.getlist('photo')
        name = request.form.get('name', 'Guest')

        if not files:
            return jsonify({'error': 'No photo uploaded'}), 400

        image_urls = [to_image_url(f) for f in files]

        prompt = """You are a professional fashion stylist and color analyst.
Look at these photo and estimate:
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
            messages=[{"role": "user", "content": content}]
        )
        print("RAW MODEL OUTPUT:", response.choices[0].message.content)
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


@app.route('/api/scan-item', methods=['POST'])
def scan_item():
    try:
        file = request.files.get('photo')
        user_id = request.form.get('user_id')

        if not file:
            return jsonify({'error': 'No photo uploaded'}), 400
        if not user_id:
            return jsonify({'error': 'Missing user_id — scan a body first to create a user.'}), 400

        file_bytes = file.read()
        file.seek(0)  # reset so to_image_url can read it again

        # Save the actual photo to disk with a unique filename
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

        save_wardrobe_item(
            user_id=user_id,
            ctype=result.get('type'),
            color=result.get('color'),
            cut=result.get('cut'),
            size='M',
            fabric=result.get('fabric'),
            availability=True,
            image_path=filename
        )

        return jsonify(result)

    except Exception as e:
        print('Error in /api/scan-item:', e)
        return jsonify({'error': 'Something went wrong scanning the item.'}), 500

@app.route('/api/wardrobe/<int:user_id>', methods=['GET'])  
def wardrobe(user_id):
    try:
        items = get_wardrobe_for_user(user_id)
        safe_items = json.loads(json.dumps(items, default=str))
        return jsonify({'items': safe_items})
    except Exception as e:
        print('Error in /api/wardrobe:', e)
        return jsonify({'error': 'Something went wrong fetching the wardrobe.'}), 500

@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/style-closet', methods=['POST'])
def style_closet():
    try:
        data = request.get_json()
        user_id = data.get('user_id')

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

        wardrobe_items = get_wardrobe_for_user(user_id)
        if not wardrobe_items:
            return jsonify({'error': 'No wardrobe items found. Add some items first.'}), 400

        item_summaries = "\n".join(
            f"id={item['id']}: {item.get('color')} {item.get('cut')} {item.get('type')} ({item.get('fabric')})"
            for item in wardrobe_items
        )

        prompt = f"""You are a professional fashion stylist.
This person has: body type = {user['bodytype']}, skintone = {user['skincolor']}.

Their wardrobe items are listed below, one per line as "id=NUMBER: description":
{item_summaries}

Suggest 2-3 outfit combinations using ONLY these items. For each outfit, provide:
- "name": a short outfit name
- "item_ids": a JSON array of the exact id numbers used (integers, copied exactly from the list above — do not invent new ids)
- "reason": a 1-2 sentence explanation of why it flatters this person's body type and coloring

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

if __name__ == '__main__':
    app.run(port=3000, debug=True)
