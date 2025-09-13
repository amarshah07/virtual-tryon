# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image
import requests
from io import BytesIO
import os
import time
import traceback
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

# --- CONFIG from env ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # service role key (secret) - required
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_KEY environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET = "images"  # ensure this exists in your Supabase project

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Xaze Try-On Backend Running 🚀"})

# Upload user image endpoint (multipart/form-data)
# Expects: 'file' form field, optional 'user_id'
@app.route("/upload_user_image", methods=["POST"])
def upload_user_image():
    try:
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400

        file = request.files["file"]
        user_id = request.form.get("user_id", "anonymous")

        # read bytes
        file_bytes = file.read()
        ext = "png"
        if "." in file.filename:
            ext = file.filename.rsplit(".", 1)[1]
        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"

        # upload bytes to supabase storage (service role key on server)
        upload_res = supabase.storage.from_(BUCKET).upload(filename, file_bytes, {"upsert": True})
        # supabase-py may return dict with error, or object; check both
        if isinstance(upload_res, dict) and upload_res.get("error"):
            return jsonify({"status": "error", "message": upload_res.get("error")}), 500

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"
        return jsonify({"status": "success", "public_url": public_url, "file_path": filename})
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

        # Simple compositing demo: scale clothing and paste on torso area
        target_w = int(user_img.width * 0.7)
        scale = target_w / max(1, cloth_img.width)
        target_h = int(cloth_img.height * scale)
        cloth_resized = cloth_img.resize((target_w, target_h), Image.LANCZOS)

        x = int((user_img.width - target_w) / 2)
        y = int(user_img.height * 0.25)

        # If cloth has alpha, use it; otherwise build mask by threshold
        mask = cloth_resized.split()[3]
        if mask.getextrema() == (0, 0):
            gray = cloth_resized.convert("L")
            mask = gray.point(lambda p: 255 if p < 250 else 0)

        composed = user_img.copy()
        composed.paste(cloth_resized, (x, y), mask)

        # Save bytes
        out_buf = BytesIO()
        composed.save(out_buf, format="PNG")
        out_buf.seek(0)
        img_bytes = out_buf.read()

        # Upload to Supabase storage
        result_filename = f"tryon_results/{user_id}_{product_id}_{int(time.time())}.png"
        upload_res = supabase.storage.from_(BUCKET).upload(result_filename, img_bytes, {"upsert": True})
        if isinstance(upload_res, dict) and upload_res.get("error"):
            return jsonify({"status": "error", "message": upload_res.get("error")}), 500

        result_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{result_filename}"

        # Save metadata into DB
        try:
            insert_res = supabase.table("tryon_results").insert({
                "user_id": user_id,
                "product_id": product_id,
                "user_image_url": user_image_url,
                "cloth_image_url": cloth_image_url,
                "result_url": result_url
            }).execute()
        except Exception as e:
            # not critical for returning result, but log
            print("DB insert error:", e)

        return jsonify({"status": "success", "result_url": result_url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
