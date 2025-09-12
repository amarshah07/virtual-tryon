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
CORS(app)  # allow all origins — fine for hackathon/dev

# --- Supabase setup ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # service role key recommended
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_KEY env variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET = "images"  # make sure this bucket exists and is public

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Xaze Try-On Backend Running 🚀"})

# --- Upload user image ---
@app.route("/upload_user_image", methods=["POST"])
def upload_user_image():
    try:
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400

        file = request.files["file"]
        user_id = request.form.get("user_id", "anonymous")
        ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"

        file_bytes = file.read()
        buf = BytesIO(file_bytes)
        buf.seek(0)

        # Upload to Supabase storage (bytes only)
        upload_res = supabase.storage.from_(BUCKET).upload(filename, buf.read())

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"
        return jsonify({"status": "success", "public_url": public_url})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Try-on endpoint ---
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

        # Download images
        user_img = Image.open(BytesIO(requests.get(user_image_url).content)).convert("RGBA")
        cloth_img = Image.open(BytesIO(requests.get(cloth_image_url).content)).convert("RGBA")

        # --- Simple try-on compositing ---
        target_w = int(user_img.width * 0.7)
        scale = target_w / max(1, cloth_img.width)
        target_h = int(cloth_img.height * scale)
        cloth_resized = cloth_img.resize((target_w, target_h), Image.LANCZOS)

        x = int((user_img.width - target_w) / 2)
        y = int(user_img.height * 0.25)

        mask = cloth_resized.split()[3]
        if mask.getextrema() == (0, 0):
            gray = cloth_resized.convert("L")
            mask = gray.point(lambda p: 255 if p < 250 else 0)

        composed = user_img.copy()
        composed.paste(cloth_resized, (x, y), mask)

        # Save PNG bytes
        out_buf = BytesIO()
        composed.save(out_buf, format="PNG")
        out_buf.seek(0)

        # Delete existing result file (optional)
        file_name = f"tryon_results/{user_id}_{product_id}.png"
        try:
            supabase.storage.from_(BUCKET).remove([file_name])
        except:
            pass

        # Upload result
        upload_res = supabase.storage.from_(BUCKET).upload(file_name, out_buf.read())
        result_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{file_name}"

        # Save metadata
        supabase.table("tryon_results").insert({
            "user_id": user_id,
            "product_id": product_id,
            "user_image_url": user_image_url,
            "cloth_image_url": cloth_image_url,
            "result_url": result_url
        }).execute()

        return jsonify({"status": "success", "result_url": result_url})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
