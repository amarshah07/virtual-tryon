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
def tryon():
    try:
        data = request.json
        user_id = data["user_id"]
        product_id = data["product_id"]
        user_image_url = data["user_image_url"]
        cloth_image_url = data["cloth_image_url"]

        # Download images
        user_img = Image.open(BytesIO(requests.get(user_image_url).content))
        cloth_img = Image.open(BytesIO(requests.get(cloth_image_url).content))

        # Gemini instruction
        instruction = """
        Overlay the clothing on the person realistically.
        Match pose, size and perspective. Clean and professional.
        """

        response = client.models.generate_content(
            model="gemini-2.5-flash-image-preview",
            contents=[cloth_img, user_img, instruction],
        )

        image_parts = [
            part.inline_data.data
            for part in response.candidates[0].content.parts
            if part.inline_data
        ]

        if not image_parts:
            return jsonify({"status": "error", "message": "No image generated"}), 400

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
