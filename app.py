# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image
import requests
from io import BytesIO
import os
import time
import traceback
import base64
# optional Google GenAI client (used in test.py flow)
try:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
    _HAS_GENAI = True
except Exception:
    genai = None
    types = None
    _HAS_GENAI = False
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

# --- CONFIG from env ---
SUPABASE_URL = "https://xetomtmbtiqwfisynrrl.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhldG9tdG1idGlxd2Zpc3lucnJsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NzM0ODk0MywiZXhwIjoyMDcyOTI0OTQzfQ.a4Oh7YnHyEqSrJrFNI3gYoGz0FUjE5aoMMCRKRDla_k"  # service role key (secret) - required 
print(SUPABASE_KEY)                                                 
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_KEY environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET = "images"  # ensure this exists in your Supabase project

# Optional Gemini configuration (set in env when available)

GEMINI_API_KEY = "AIzaSyCxAcqc8gBVOMAlO0veJPjmBch1kWBQpgI"
# Mode: 'base64' (default) or 'multipart' - how to send image to Gemini
GEMINI_MODE = os.environ.get("GEMINI_MODE", "base64")

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

        # Diagnostic logging to help debug header/value issues
        try:
            print("upload_user_image: file attrs -> filename:", getattr(file, 'filename', None), "content_type:", getattr(file, 'content_type', None))
        except Exception:
            print("upload_user_image: unable to read file attrs")
        print("upload_user_image: form keys:", list(request.form.keys()))

        # read bytes
        file_bytes = file.read()
        ext = "png"
        if "." in file.filename:
            ext = file.filename.rsplit(".", 1)[1]
        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"

        # upload bytes to supabase storage (service role key on server)
        try:
            # pass raw bytes (supabase client expects str/bytes/os.PathLike)
            upload_res = supabase.storage.from_(BUCKET).upload(filename, file_bytes, {"upsert": "true"})
            # supabase-py may return dict with error, or object; check both
            if isinstance(upload_res, dict) and upload_res.get("error"):
                print("Supabase upload error (upload_user_image):", upload_res)
                return jsonify({"status": "error", "message": upload_res.get("error")}), 500
        except Exception as e:
            traceback.print_exc()
            print("Supabase upload exception class:", e.__class__, "repr:", repr(e))
            return jsonify({"status": "error", "message": str(e), "type": str(e.__class__)}), 500

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

        # Basic validation + clear error messages
        print("tryon payload:", data)

        if not user_id or not product_id:
            return jsonify({"status": "error", "message": "user_id and product_id required"}), 400

        # Ensure image fields are strings (public URLs). Reject booleans or other types early.
        if not user_image_url or not cloth_image_url:
            return jsonify({"status": "error", "message": "Image URLs missing"}), 400
        if not isinstance(user_image_url, str):
            return jsonify({"status": "error", "message": "user_image_url must be a string (public URL).", "type": str(type(user_image_url))}), 400
        if not isinstance(cloth_image_url, str):
            return jsonify({"status": "error", "message": "cloth_image_url must be a string (public URL).", "type": str(type(cloth_image_url))}), 400

        # Download both images (timeout)
        try:
            uresp = requests.get(user_image_url, timeout=15)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Failed to fetch user image: {e}"}), 400

        try:
            cresp = requests.get(cloth_image_url, timeout=15)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Failed to fetch cloth image: {e}"}), 400

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

        # If configured, send composed image bytes to Gemini (optional)

        def send_to_gemini_images(user_image: Image.Image, cloth_image: Image.Image, instruction: str = None):
            """
            Send PIL images + instruction to Gemini using the genai client (same as test.py).
            This implementation requires `google-genai` and `GEMINI_API_KEY` set in env.
            Returns dict with either 'image_bytes' (PNG) or 'raw' or 'error'.
            """
            if not GEMINI_API_KEY:
                return {"error": "GEMINI_API_KEY not set in environment"}

            if not _HAS_GENAI or genai is None:
                return {"error": "google.genai client not installed (pip install google-genai)"}

            try:
                client = genai.Client(api_key=GEMINI_API_KEY)
                model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-image-preview")
                if instruction is None:
                    instruction = (
                        "Overlay the given clothing item onto the person realistically,"
                        " making it look like they are wearing it. Keep it clean and professional."
                    )

                print("Sending images to Gemini via genai client, model:", model_name)
                response = client.models.generate_content(
                    model=model_name,
                    contents=[cloth_image, user_image, instruction],
                )

                # Extract inline image bytes like test.py
                try:
                    image_parts = [
                        part.inline_data.data
                        for part in response.candidates[0].content.parts
                        if getattr(part, "inline_data", None)
                    ]
                except Exception:
                    image_parts = []

                if image_parts:
                    try:
                        out_img = Image.open(BytesIO(image_parts[0]))
                        out_buf2 = BytesIO()
                        out_img.save(out_buf2, format="PNG")
                        out_buf2.seek(0)
                        print("Received image parts from Gemini and normalized to PNG")
                        return {"image_bytes": out_buf2.read(), "raw": response}
                    except Exception:
                        print("Received image parts but failed to normalize; returning raw bytes")
                        return {"image_bytes": image_parts[0], "raw": response}

                return {"raw": response}
            except Exception as e:
                traceback.print_exc()
                return {"error": str(e)}

        # Fire and log Gemini result (try genai client first)
        try:
            gemini_resp = send_to_gemini_images(
                user_img,
                cloth_img,
                instruction=(
                    "Overlay the given clothing item onto the person realistically, "
                    "making it look like they are actually wearing it. "
                    "Keep it clean and professional. "
                    "Check the fit and adjust the clothing item to match the person's pose and body shape. "
                    "Align clothing with shoulders, arms, and torso. "
                    "Preserve correct proportions, blend shadows and lighting naturally. "
                    "Do not alter the person’s face, skin, or background. "
                    "Return only the final composite try-on image."
                )
            )
            print("gemini_resp:", gemini_resp)
        except Exception:
            traceback.print_exc()

        # Upload to Supabase storage
        result_filename = f"tryon_results/{user_id}_{product_id}_{int(time.time())}.png"
        # pass upsert as a string to avoid boolean values being used as header values
        try:
            upload_res = supabase.storage.from_(BUCKET).upload(result_filename, img_bytes, {"upsert": "true"})
            if isinstance(upload_res, dict) and upload_res.get("error"):
                print("Supabase upload error (tryon result):", upload_res)
                return jsonify({"status": "error", "message": upload_res.get("error")}), 500
        except Exception as e:
            traceback.print_exc()
            print("Supabase upload exception class (tryon result):", e.__class__, "repr:", repr(e))
            return jsonify({"status": "error", "message": str(e), "type": str(e.__class__)}), 500

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
