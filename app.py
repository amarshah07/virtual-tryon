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
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # service role key (secret) - required
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
@@ -36,6 +53,13 @@ def upload_user_image():
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
@@ -44,10 +68,17 @@ def upload_user_image():
        filename = f"user_uploads/{user_id}_{int(time.time())}.{ext}"

        # upload bytes to supabase storage (service role key on server)
        upload_res = supabase.storage.from_(BUCKET).upload(filename, file_bytes, {"upsert": True})
        # supabase-py may return dict with error, or object; check both
        if isinstance(upload_res, dict) and upload_res.get("error"):
            return jsonify({"status": "error", "message": upload_res.get("error")}), 500
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
@@ -66,14 +97,33 @@ def tryon():
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
        uresp = requests.get(user_image_url, timeout=15)
        cresp = requests.get(cloth_image_url, timeout=15)
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
@@ -106,11 +156,81 @@ def tryon():
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
            gemini_resp = send_to_gemini_images(user_img, cloth_img)
            print("gemini_resp:", gemini_resp)
        except Exception:
            traceback.print_exc()

        # Upload to Supabase storage
        result_filename = f"tryon_results/{user_id}_{product_id}_{int(time.time())}.png"
        upload_res = supabase.storage.from_(BUCKET).upload(result_filename, img_bytes, {"upsert": True})
        if isinstance(upload_res, dict) and upload_res.get("error"):
            return jsonify({"status": "error", "message": upload_res.get("error")}), 500
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
