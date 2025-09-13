#!/usr/bin/env python3
# app.py - Fixed version: uses Gemini properly (sends bytes + strong prompt)
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image
import requests
from io import BytesIO
import os
import time
import traceback
import base64
import json

# optional Google GenAI client
try:
    from google import genai  # type: ignore
    _HAS_GENAI = True
except Exception:
    genai = None
    _HAS_GENAI = False

from supabase import create_client, Client

app = Flask(_name_)
CORS(app)

# --- CONFIG (use environment variables in production) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://xetomtmbtiqwfisynrrl.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhldG9tdG1idGlxd2Zpc3lucnJsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NzM0ODk0MywiZXhwIjoyMDcyOTI0OTQzfQ.a4Oh7YnHyEqSrJrFNI3gYoGz0FUjE5aoMMCRKRDla_k")
BUCKET = os.environ.get("SUPABASE_BUCKET", "images")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCxAcqc8gBVOMAlO0veJPjmBch1kWBQpgI")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-image-preview")
GEMINI_MODE = os.environ.get("GEMINI_MODE", "base64")  # not used heavily here

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_KEY environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Helpers ---
def _pil_to_bytes(pil_img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = BytesIO()
    pil_img.save(buf, format=fmt)
    buf.seek(0)
    return buf.read()

def _bytes_to_pil(b: bytes) -> Image.Image:
    return Image.open(BytesIO(b)).convert("RGBA")

def _upload_to_supabase(bucket: str, path: str, file_bytes: bytes, content_type: str = "image/png") -> str:
    """
    Upload raw bytes to Supabase storage and return public URL.
    """
    try:
        # supabase client expects bytes for upload
        upload_res = supabase.storage.from_(bucket).upload(path, file_bytes, {"upsert": "true"})
        if isinstance(upload_res, dict) and upload_res.get("error"):
            raise RuntimeError(f"Supabase upload error: {upload_res.get('error')}")
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
        return public_url
    except Exception as e:
        # bubble up
        raise

def send_to_gemini_images(user_image: Image.Image, cloth_image: Image.Image, instruction: str = None):
    """
    Send PNG bytes for cloth + user with a textual instruction to Gemini (via google.genai).
    Returns dict: {'image_bytes': b'...','raw': response} OR {'raw': response} OR {'error': '...'}
    """
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set in environment"}

    if not _HAS_GENAI or genai is None:
        return {"error": "google-genai client not installed (pip install google-genai)"}

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        model_name = GEMINI_MODEL or "gemini-2.5-flash-image-preview"
        if instruction is None:
            instruction = (
                "Perform a virtual clothing try-on. Carefully overlay the clothing item "
                "onto the person in a realistic manner. Pay close attention to:\n"
                "1. Body proportions and pose alignment\n"
                "2. Natural lighting and shadows\n"
                "3. Proper fit around shoulders, arms, and torso\n"
                "4. Do not cover the face or significantly alter the background\n"
                "5. Maintain the original image quality and resolution\n"
                "Return only the final composite image with the clothing properly fitted."
            )

        # Convert PIL images to PNG bytes (cloth first, then person - explicit)
        cloth_bytes = _pil_to_bytes(cloth_image, fmt="PNG")
        user_bytes = _pil_to_bytes(user_image, fmt="PNG")

        print("Calling Gemini model:", model_name)
        response = client.models.generate_content(
            model=model_name,
            contents=[
                "This is a clothing item:",
                cloth_bytes,
                "This is a person:",
                user_bytes,
                instruction
            ],
        )

        # Try to extract inline image bytes
        image_parts = []
        try:
            # response.candidates[0].content.parts may contain inline_data
            for part in response.candidates[0].content.parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    image_parts.append(inline.data)
        except Exception:
            image_parts = []

        if image_parts:
            try:
                normalized = Image.open(BytesIO(image_parts[0])).convert("RGBA")
                buf = BytesIO()
                normalized.save(buf, format="PNG")
                buf.seek(0)
                return {"image_bytes": buf.read(), "raw": response}
            except Exception:
                # return raw bytes if normalization fails
                return {"image_bytes": image_parts[0], "raw": response}

        return {"raw": response}
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}

# --- Routes ---
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Xaze Try-On Backend Running 🚀"})

@app.route("/upload_user_image", methods=["POST"])
def upload_user_image():
    try:
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400

        file = request.files["file"]
        user_id = request.form.get("user_id", "anonymous")

        file_bytes = file.read()
        ext = "png"
        if "." in getattr(file, "filename", ""):
            ext = file.filename.rsplit(".", 1)[1].lower()
            if ext not in ("png", "jpg", "jpeg", "webp"):
                ext = "png"
        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"

        try:
            upload_res = supabase.storage.from_(BUCKET).upload(filename, file_bytes, {"upsert": "true"})
            if isinstance(upload_res, dict) and upload_res.get("error"):
                return jsonify({"status": "error", "message": upload_res.get("error")}), 500
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e), "type": str(e._class_)}), 500

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"
        return jsonify({"status": "success", "public_url": public_url, "file_path": filename})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/tryon", methods=["POST"])
def tryon():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user_id")
        product_id = data.get("product_id")
        user_image_url = data.get("user_image_url")
        cloth_image_url = data.get("cloth_image_url")
        custom_instruction = data.get("instruction")  # optional override

        print("tryon payload:", data)
        if not user_id or not product_id:
            return jsonify({"status": "error", "message": "user_id and product_id required"}), 400
        if not user_image_url or not cloth_image_url:
            return jsonify({"status": "error", "message": "user_image_url and cloth_image_url required"}), 400

        # Download images
        try:
            uresp = requests.get(user_image_url, timeout=20)
            uresp.raise_for_status()
            cresp = requests.get(cloth_image_url, timeout=20)
            cresp.raise_for_status()
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Failed to download images: {e}"}), 400

        try:
            user_img = Image.open(BytesIO(uresp.content)).convert("RGBA")
            cloth_img = Image.open(BytesIO(cresp.content)).convert("RGBA")
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Invalid image data: {e}"}), 400

        # Debug image info
        print(f"User image size: {user_img.size}, mode: {user_img.mode}")
        print(f"Cloth image size: {cloth_img.size}, mode: {cloth_img.mode}")

        # Prepare a local PIL fallback composed image (in case Gemini fails)
        try:
            base = user_img.copy()
            
            # Calculate better positioning based on human proportions
            # Assuming the clothing is a top (shirt, t-shirt, etc.)
            shoulder_width = int(base.width * 0.3)  # Estimate shoulder width
            scale = shoulder_width / max(1, cloth_img.width)
            target_w = int(cloth_img.width * scale)
            target_h = int(cloth_img.height * scale)
            cloth_resized = cloth_img.resize((target_w, target_h), Image.LANCZOS)
            
            # Better positioning - center on torso area
            x = int((base.width - target_w) / 2)
            y = int(base.height * 0.15)  # Start from upper body
            
            # Create a proper mask for transparency
            if cloth_resized.mode == 'RGBA':
                mask = cloth_resized.split()[3]
            else:
                # Create a mask from transparency or use the image itself
                gray = cloth_resized.convert("L")
                mask = gray.point(lambda p: 255 if p < 250 else 0)
            
            composed = base.copy()
            composed.paste(cloth_resized, (x, y), mask)
            fallback_bytes = _pil_to_bytes(composed, fmt="PNG")
        except Exception as e:
            print(f"Fallback composition error: {e}")
            traceback.print_exc()
            fallback_bytes = None

        # Try Gemini (preferred)
        final_image_bytes = None
        used_backend = None

        if _HAS_GENAI and GEMINI_API_KEY:
            try:
                gemini_resp = send_to_gemini_images(
                    user_img,
                    cloth_img,
                    instruction=custom_instruction
                )
                print("gemini_resp keys:", list(gemini_resp.keys()))
            except Exception:
                traceback.print_exc()
                gemini_resp = {"error": "Exception calling Gemini"}

            if gemini_resp.get("image_bytes"):
                final_image_bytes = gemini_resp["image_bytes"]
                used_backend = "gemini"
            else:
                # debug messages to logs
                if gemini_resp.get("error"):
                    print("Gemini returned error:", gemini_resp.get("error"))
                else:
                    print("Gemini returned no inline image. gemini_resp keys:", list(gemini_resp.keys()))
        else:
            print("Gemini client not available or GEMINI_API_KEY not set. Skipping Gemini.")

        # If Gemini failed, use fallback PIL composition (if available)
        if final_image_bytes is None:
            if fallback_bytes:
                final_image_bytes = fallback_bytes
                used_backend = "local_pil"
            else:
                return jsonify({"status": "error", "message": "Both Gemini and local fallback failed"}), 500

        # Upload to Supabase storage
        result_filename = f"tryon_results/{user_id}{product_id}{int(time.time())}.png"
        try:
            upload_res = supabase.storage.from_(BUCKET).upload(result_filename, final_image_bytes, {"upsert": "true"})
            if isinstance(upload_res, dict) and upload_res.get("error"):
                print("Supabase upload error (tryon result):", upload_res)
                return jsonify({"status": "error", "message": upload_res.get("error")}), 500
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e), "type": str(e._class_)}), 500

        result_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{result_filename}"

        # Save metadata into DB (non-critical)
        try:
            supabase.table("tryon_results").insert({
                "user_id": user_id,
                "product_id": product_id,
                "user_image_url": user_image_url,
                "cloth_image_url": cloth_image_url,
                "result_url": result_url,
                "used_backend": used_backend
            }).execute()
        except Exception as e:
            print("DB insert error:", e)

        return jsonify({"status": "success", "result_url": result_url, "used_backend": used_backend})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

if _name_ == "_main_":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
