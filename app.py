from flask import Flask, request, jsonify
from PIL import Image
import requests
from io import BytesIO
from supabase import create_client
from google import genai
import os

app = Flask(__name__)

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

@app.route("/tryon", methods=["POST"])
@app.route("/tryon", methods=["POST"])
def tryon():
    try:
        data = request.json
        user_id = data.get("user_id")  # ✅ won’t crash if missing
        product_id = data.get("product_id")
        user_image_url = data.get("user_image_url")
        cloth_image_url = data.get("cloth_image_url")

        if not user_image_url or not cloth_image_url:
            return jsonify({"status": "error", "message": "Image URLs missing"}), 400

        # your processing...
        result_url = "https://your-supabase-bucket/tryon_results/sample.png"

        return jsonify({"status": "success", "result_url": result_url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


        output = Image.open(BytesIO(image_parts[0]))

        # Save to Supabase Storage
        file_name = f"tryon_results/{user_id}_{product_id}.png"
        buf = BytesIO()
        output.save(buf, format="PNG")
        buf.seek(0)

        supabase.storage.from_("images").upload(file_name, buf)
        result_url = f"{SUPABASE_URL}/storage/v1/object/public/images/{file_name}"

        # Save metadata to DB
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

@app.route("/", methods=["GET"])
def home():
    return "Xaze Try-On Backend Running 🚀"

