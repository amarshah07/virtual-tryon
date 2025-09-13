# main.py
import os, time, traceback, requests
from flask import Flask, request, jsonify
from io import BytesIO
from PIL import Image
from supabase import create_client, Client
from flask_cors import CORS

# ---------- CONFIG ----------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://xetomtmbtiqwfisynrrl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "YOUR_SUPABASE_KEY_HERE")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "images")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(_name_)
CORS(app)  # Allow mobile app to call

# ---------- HELPERS ----------

def safe_resize_keep_aspect(img: Image.Image, target_w=None, target_h=None):
    w, h = img.size
    if target_w:
        ratio = target_w / float(w)
        target_h = int(h * ratio)
    elif target_h:
        ratio = target_h / float(h)
        target_w = int(w * ratio)
    return img.resize((target_w, target_h), Image.LANCZOS)

def pil_to_bytes(img: Image.Image, fmt="PNG"):
    buf = BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf.read()

def local_tryon_fallback(person_img: Image.Image, cloth_img: Image.Image):
    base = person_img.copy().convert("RGBA")
    cloth = cloth_img.copy().convert("RGBA")

    target_w = int(base.width * 0.7)
    cloth_resized = safe_resize_keep_aspect(cloth, target_w=target_w)

    y_offset = int(base.height * 0.22)
    x_offset = int((base.width - cloth_resized.width) / 2)

    mask = None
    if cloth_resized.mode == "RGBA":
        mask = cloth_resized.split()[3]
        if mask.getextrema() == (0, 0):
            mask = None
    if mask is None:
        gray = cloth_resized.convert("L")
        mask = gray.point(lambda p: 255 if p < 250 else 0)

    composed = base.copy()
    composed.paste(cloth_resized, (x_offset, y_offset), mask)
    return pil_to_bytes(composed, fmt="PNG")

# ---------- ROUTES ----------

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Virtual Try-On Backend Running"})

@app.route("/upload_user_image", methods=["POST"])
def upload_user_image():
    try:
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400

        file = request.files["file"]
        user_id = request.form.get("user_id", "anonymous")
        file_bytes = file.read()
        ext = "png"
        if getattr(file, "filename", None) and "." in file.filename:
            ext = file.filename.rsplit(".", 1)[1].lower()
        if ext not in ("png", "jpg", "jpeg", "webp"):
            ext = "png"

        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"
        supabase.storage.from_(SUPABASE_BUCKET).upload(filename, file_bytes, {"upsert": "true"})

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
        return jsonify({"status": "success", "public_url": public_url, "file_path": filename})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route("/tryon", methods=["POST"])
def tryon():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user_id")
        product_id = data.get("product_id")
        user_image_url = data.get("user_image_url")
        cloth_image_url = data.get("cloth_image_url")

        if not user_id or not product_id or not user_image_url or not cloth_image_url:
            return jsonify({"status": "error", "message": "Missing fields"}), 400

        # Download both images
        uresp = requests.get(user_image_url, timeout=20)
        cresp = requests.get(cloth_image_url, timeout=20)
        person_img = Image.open(BytesIO(uresp.content)).convert("RGBA")
        cloth_img = Image.open(BytesIO(cresp.content)).convert("RGBA")

        # Fallback try-on
        final_image_bytes = local_tryon_fallback(person_img, cloth_img)

        result_filename = f"tryon_results/{user_id}{product_id}{int(time.time())}.png"
        supabase.storage.from_(SUPABASE_BUCKET).upload(result_filename, final_image_bytes, {"upsert": "true"})
        result_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{result_filename}"

        # Save to DB table (optional)
        try:
            supabase.table("tryon_results").insert({
                "user_id": user_id,
                "product_id": product_id,
                "user_image_url": user_image_url,
                "cloth_image_url": cloth_image_url,
                "result_url": result_url
            }).execute()
        except Exception as e:
            print("DB insert skipped:", e)

        return jsonify({"status": "success", "result_url": result_url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

if _name_ == "_main_":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
