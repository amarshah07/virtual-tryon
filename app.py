#!/usr/bin/env python3
"""
app.py - Virtual Try-On Flask backend (single-file)

Environment variables:
  SUPABASE_URL        - e.g. https://yourproject.supabase.co
  SUPABASE_KEY        - Supabase service_role or anon key (server must hold service key)
  SUPABASE_BUCKET     - storage bucket name (default: "images")
  GEMINI_API_KEY      - optional Google Gemini (GenAI) API key
  GEMINI_MODEL        - optional Gemini model name (default used if unset)
  PORT                - optional port (default 5000)

Endpoints:
  GET  /                 - health check
  POST /upload_user_image - form-data file upload (multipart/form-data)
  POST /tryon            - JSON { user_id, product_id, user_image_url, cloth_image_url, instruction OPTIONAL }
"""

import os
import time
import traceback
from io import BytesIO
from typing import Optional

from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image, ImageOps
import requests

# Optional google-genai client
try:
    from google import genai  # type: ignore
    _HAS_GENAI = True
except Exception:
    genai = None
    _HAS_GENAI = False

# Supabase client
from supabase import create_client, Client

app = Flask(_name_)
CORS(app)

# ----------------- Configuration -----------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "images")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-image-preview")

# Validate required config
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set as environment variables")

# Initialize supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------------- Helpers -----------------
def pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf.read()

def bytes_to_pil(b: bytes) -> Image.Image:
    return Image.open(BytesIO(b)).convert("RGBA")

def safe_resize_keep_aspect(img: Image.Image, target_w: Optional[int] = None, target_h: Optional[int] = None) -> Image.Image:
    """
    Resize while preserving aspect ratio. Provide either target_w or target_h or both.
    """
    if target_w and target_h:
        return ImageOps.contain(img, (target_w, target_h))
    if target_w:
        wpercent = target_w / float(img.width)
        hsize = int(float(img.height) * wpercent)
        return img.resize((target_w, hsize), Image.LANCZOS)
    if target_h:
        hpercent = target_h / float(img.height)
        wsize = int(float(img.width) * hpercent)
        return img.resize((wsize, target_h), Image.LANCZOS)
    return img

def download_image_to_pil(url: str, timeout: int = 20) -> Image.Image:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def upload_bytes_to_supabase(bucket: str, path: str, data: bytes, content_type: str = "image/png") -> str:
    """
    Upload bytes to Supabase storage and return public URL.
    """
    try:
        # supabase python client accepts bytes for upload
        res = supabase.storage.from_(bucket).upload(path, data, {"upsert": "true"})
        # Some supabase client versions return a dict with an 'error' key on failure
        if isinstance(res, dict) and res.get("error"):
            raise RuntimeError(res.get("error"))
    except Exception:
        # fallback: re-raise to be handled by caller
        raise
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
    return public_url

# ----------------- Gemini Integration -----------------
def send_to_gemini_images(person_img: Image.Image, cloth_img: Image.Image, instruction: Optional[str] = None):
    """
    Send images + instruction to Gemini via google-genai client (if installed).
    Returns dict with potential keys:
      - image_bytes: bytes of resulting image (PNG)
      - raw: raw response object
      - error: error string
    """
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}
    if not _HAS_GENAI or genai is None:
        return {"error": "google-genai client not installed (pip install google-genai)"}

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        model_name = GEMINI_MODEL or "gemini-2.5-flash-image-preview"

        if instruction is None:
            instruction = (
                "Overlay the given clothing item onto the person realistically, "
                "align to shoulders and torso, preserve face and background, blend shadows and lighting naturally, "
                "return only the final composited image as inline image data."
            )

        cloth_bytes = pil_to_bytes(cloth_img, fmt="PNG")
        person_bytes = pil_to_bytes(person_img, fmt="PNG")

        # This usage depends on genai API; treat as a thin wrapper - examine raw if issues
        response = client.models.generate_content(
            model=model_name,
            contents=[cloth_bytes, person_bytes, instruction],
        )

        # Try to extract inline image bytes from response
        image_parts = []
        try:
            for part in response.candidates[0].content.parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    image_parts.append(inline.data)
        except Exception:
            image_parts = []

        if image_parts:
            # Attempt to normalize into PNG bytes
            try:
                pil_out = Image.open(BytesIO(image_parts[0])).convert("RGBA")
                out_buf = pil_to_bytes(pil_out, fmt="PNG")
                return {"image_bytes": out_buf, "raw": response}
            except Exception:
                return {"image_bytes": image_parts[0], "raw": response}

        return {"raw": response}
    except Exception as exc:
        traceback.print_exc()
        return {"error": str(exc)}

# ----------------- Local PIL fallback -----------------
def local_tryon_fallback(person_img: Image.Image, cloth_img: Image.Image) -> bytes:
    """
    Simple heuristic composition as graceful fallback.
    Scales cloth to ~70% of person's width, places with an upper-body offset.
    """
    base = person_img.copy().convert("RGBA")
    cloth = cloth_img.copy().convert("RGBA")

    # Scale cloth to ~70% of person's width
    target_w = int(base.width * 0.7)
    cloth_resized = safe_resize_keep_aspect(cloth, target_w=target_w)

    # Heuristic y offset: 22% from top
    y_offset = int(base.height * 0.22)
    x_offset = int((base.width - cloth_resized.width) / 2)

    # If cloth has alpha, use it; otherwise build mask from luminosity
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

# ----------------- Routes -----------------
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Virtual Try-On Backend OK"})

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

        try:
            res = supabase.storage.from_(SUPABASE_BUCKET).upload(filename, file_bytes, {"upsert": "true"})
            if isinstance(res, dict) and res.get("error"):
                return jsonify({"status": "error", "message": res.get("error")}), 500
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e)}), 500

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
        custom_instruction = data.get("instruction")

        # Basic validation
        if not user_id or not product_id:
            return jsonify({"status": "error", "message": "user_id and product_id are required"}), 400
        if not user_image_url or not cloth_image_url:
            return jsonify({"status": "error", "message": "user_image_url and cloth_image_url are required"}), 400

        # Download images
        try:
            uresp = requests.get(user_image_url, timeout=20)
            uresp.raise_for_status()
            cresp = requests.get(cloth_image_url, timeout=20)
            cresp.raise_for_status()
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Failed to download images: {e}"}), 400

        # Load into PIL and ensure RGBA
        try:
            person_img = Image.open(BytesIO(uresp.content)).convert("RGBA")
            cloth_img = Image.open(BytesIO(cresp.content)).convert("RGBA")
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Invalid image data: {e}"}), 400

        # Normalize large images to limit cost/time
        MAX_DIM = 1600
        if max(person_img.width, person_img.height) > MAX_DIM:
            if person_img.width >= person_img.height:
                person_img = safe_resize_keep_aspect(person_img, target_w=MAX_DIM)
            else:
                person_img = safe_resize_keep_aspect(person_img, target_h=MAX_DIM)
        if max(cloth_img.width, cloth_img.height) > MAX_DIM:
            if cloth_img.width >= cloth_img.height:
                cloth_img = safe_resize_keep_aspect(cloth_img, target_w=MAX_DIM)
            else:
                cloth_img = safe_resize_keep_aspect(cloth_img, target_h=MAX_DIM)

        final_image_bytes = None
        used_backend = None

        # Attempt Gemini first (preferred)
        try:
            if _HAS_GENAI and GEMINI_API_KEY:
                gemini_resp = send_to_gemini_images(person_img, cloth_img, instruction=custom_instruction)
                if gemini_resp.get("image_bytes"):
                    final_image_bytes = gemini_resp["image_bytes"]
                    used_backend = "gemini"
                else:
                    used_backend = "gemini_failed"
                    if gemini_resp.get("error"):
                        print("Gemini error:", gemini_resp.get("error"))
                    else:
                        print("Gemini returned no inline image. Raw keys:", list(gemini_resp.keys()))
            else:
                print("Gemini client not available or GEMINI_API_KEY not set - skipping Gemini")
        except Exception:
            traceback.print_exc()
            used_backend = "gemini_exception"

        # Fallback to local composition if Gemini failed or not available
        if final_image_bytes is None:
            try:
                final_image_bytes = local_tryon_fallback(person_img, cloth_img)
                if used_backend in (None, "gemini_failed", "gemini_exception"):
                    used_backend = "local_pil"
                else:
                    used_backend = used_backend or "local_pil"
            except Exception:
                traceback.print_exc()
                return jsonify({"status": "error", "message": "Both Gemini and local fallback failed"}), 500

        # Upload result to Supabase storage
        try:
            timestamp = int(time.time())
            result_filename = f"tryon_results/{user_id}{product_id}{timestamp}.png"
            try:
                result_url = upload_bytes_to_supabase(SUPABASE_BUCKET, result_filename, final_image_bytes)
            except Exception:
                # fallback to direct client call
                upload_res = supabase.storage.from_(SUPABASE_BUCKET).upload(result_filename, final_image_bytes, {"upsert": "true"})
                if isinstance(upload_res, dict) and upload_res.get("error"):
                    raise RuntimeError(upload_res.get("error"))
                result_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{result_filename}"
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": f"Failed to upload result image: {e}"}), 500

        # Save metadata into DB (best-effort)
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
            print("Warning: DB insert failed:", e)
            traceback.print_exc()

        return jsonify({"status": "success", "result_url": result_url, "used_backend": used_backend})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------- App entrypoint -----------------
if _name_ == "_main_":
    port = int(os.environ.get("PORT", 5000))
    # For development only; in production use gunicorn/uvicorn
    app.run(host="0.0.0.0", port=port, debug=True)
