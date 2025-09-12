# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image
import requests
from io import BytesIO
import os
from supabase import create_client, Client
import traceback
import time

app = Flask(__name__)
CORS(app)  # allow all origins — fine for dev/hackathon

# --- Supabase (service role key must be set in env) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # service role key recommended for server
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_KEY env variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# bucket name (make sure it exists)
BUCKET = "images"


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Xaze Try-On Backend Running 🚀"})


# Upload user image endpoint (multipart/form-data)
@app.route("/upload_user_image", methods=["POST"])
def upload_user_image():
    try:
        # Expect 'file' and optional 'user_id' in the form
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400

        file = request.files["file"]
        user_id = request.form.get("user_id", "anonymous")
        ext = (file.filename.split(".")[-1] if "." in file.filename else "jpg")
        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"

        # read bytes
        file_bytes = file.read()
        buf = BytesIO(file_bytes)
        buf.seek(0)

        # upload to Supabase storage (server-side using service role key)
        res = supabase.storage.from_(BUCKET).upload(filename, buf, {"upsert": True})
        # upload returns an object; check for errors
        if isinstance(res, dict) and res.get("error"):
            return jsonify({"status": "error", "message": res.get("error")}), 500

        # construct public url (works if bucket is public)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"
        return jsonify({"status": "success", "public_url": public_url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# Try-on endpoint: expects JSON -> user_id, product_id, user_image_url, cloth_image_url
@app.route("/tryon", methods=["POST"])
def tryon():
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        product_id = data.get("product_id")
        user_image_url = data.get("user_image_url")
        cloth_image_url = data.get("cloth_image_url")

        if not user_id or not product_id:
            return jsonify({"status": "error", "message": "user_id and product_id required"}), 400
        if not user_image_url or not cloth_image_url:
            return jsonify({"status": "error", "message": "Image URLs missing"}), 400

        # Download both images (timeout)
        uresp = requests.get(user_image_url, timeout=15)
        cresp = requests.get(cloth_image_url, timeout=15)
        if uresp.status_code != 200:
            return jsonify({"status": "error", "message": f"Failed to fetch user image: {uresp.status_code}"}), 400
        if cresp.status_code != 200:
            return jsonify({"status": "error", "message": f"Failed to fetch cloth image: {cresp.status_code}"}), 400

        user_img = Image.open(BytesIO(uresp.content)).convert("RGBA")
        cloth_img = Image.open(BytesIO(cresp.content)).convert("RGBA")

        # Simple compositing algorithm (works as a demo)
        #  - scale clothing to ~70% of user width, preserve aspect ratio
        target_w = int(user_img.width * 0.7)
        scale = target_w / max(1, cloth_img.width)
        target_h = int(cloth_img.height * scale)
        cloth_resized = cloth_img.resize((target_w, target_h), Image.LANCZOS)

        # Where to place: horizontally center, vertical at about 25% of user height
        x = int((user_img.width - target_w) / 2)
        y = int(user_img.height * 0.25)

        # Create mask from alpha or from non-white content if alpha is empty
        mask = cloth_resized.split()[3]
        # if mask is all zero (no alpha), build a mask from luminance (non-white areas)
        if mask.getextrema() == (0, 0):
            gray = cloth_resized.convert("L")
            # threshold: pixels that are near-white become transparent
            mask = gray.point(lambda p: 255 if p < 250 else 0)

        # Paste with mask
        composed = user_img.copy()
        composed.paste(cloth_resized, (x, y), mask)

        # Finalize: convert to PNG bytes
        out_buf = BytesIO()
        composed = composed.convert("RGBA")
        composed.save(out_buf, format="PNG")
        out_buf.seek(0)

        # Upload result to Supabase (service role)
        file_name = f"tryon_results/{user_id}_{product_id}_{int(time.time())}.png"
        upload_res = supabase.storage.from_(BUCKET).upload(file_name, out_buf, {"upsert": True})
        if isinstance(upload_res, dict) and upload_res.get("error"):
            return jsonify({"status": "error", "message": upload_res.get("error")}), 500

        result_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{file_name}"

        # Save metadata in DB
        insert_res = supabase.table("tryon_results").insert({
            "user_id": user_id,
            "product_id": product_id,
            "user_image_url": user_image_url,
            "cloth_image_url": cloth_image_url,
            "result_url": result_url
        }).execute()

        # check for errors in insert_res
        # supabase-py returns a dict-like (response). We'll assume it's fine for now.

        return jsonify({"status": "success", "result_url": result_url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # in dev, run: python app.py  (and ensure you use host 0.0.0.0 if testing on device)
    app.run(host="0.0.0.0", port=port, debug=True)
