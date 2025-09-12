from flask import Flask, request, jsonify
from PIL import Image
import requests
from io import BytesIO
import base64
import os
from supabase import create_client, Client
import google.generativeai as genai

app = Flask(__name__)

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

def image_to_base64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Xaze Try-On Backend Running 🚀"})

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

        # Download input images
        user_img = Image.open(BytesIO(requests.get(user_image_url).content))
        cloth_img = Image.open(BytesIO(requests.get(cloth_image_url).content))

        # Convert to base64
        user_b64 = image_to_base64(user_img)
        cloth_b64 = image_to_base64(cloth_img)

        # Gemini prompt
        prompt = "Overlay the clothing item onto the person realistically."

        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content([
            {"mime_type": "image/png", "data": base64.b64decode(cloth_b64)},
            {"mime_type": "image/png", "data": base64.b64decode(user_b64)},
            prompt,
        ])

        img_data = None
        if response and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data.data:
                    img_data = base64.b64decode(part.inline_data.data)
                    break

        # If Gemini fails → just return cloth image for demo
        if not img_data:
            buf = BytesIO()
            cloth_img.save(buf, format="PNG")
            buf.seek(0)
            img_data = buf.getvalue()

        result_img = Image.open(BytesIO(img_data))

        # Save to Supabase Storage
        file_name = f"tryon_results/{user_id}_{product_id}.png"
        buf = BytesIO()
        result_img.save(buf, format="PNG")
        buf.seek(0)
        supabase.storage.from_("images").upload(file_name, buf, {"upsert": True})

        # Public URL
        result_url = f"{SUPABASE_URL}/storage/v1/object/public/images/{file_name}"

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
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
