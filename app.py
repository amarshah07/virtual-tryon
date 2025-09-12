from flask import Flask, request, jsonify
import os
from supabase import create_client
from google import genai
from PIL import Image
import requests
from io import BytesIO
import base64

app = Flask(__name__)

# --- Supabase setup ---
SUPABASE_URL = "https://xetomtmbtiqwfisynrrl.supabase.co";
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhldG9tdG1idGlxd2Zpc3lucnJsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTczNDg5NDMsImV4cCI6MjA3MjkyNDk0M30.eJNpLnTwzLyCIEVjwSzh3K1N4Y0mA9HV914pY6q3nRo";

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Gemini setup ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

# --- Utility: Convert image to base64 ---
def image_to_base64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

@app.route("/tryon", methods=["POST"])
def tryon():
    try:
        data = request.json
        user_id = data["user_id"]
        product_id = data["product_id"]
        user_image_url = data["user_image_url"]
        cloth_image_url = data["cloth_image_url"]

        # --- Download images ---
        user_img = Image.open(BytesIO(requests.get(user_image_url).content))
        cloth_img = Image.open(BytesIO(requests.get(cloth_image_url).content))

        # Convert images to base64 for Gemini API
        user_img_b64 = image_to_base64(user_img)
        cloth_img_b64 = image_to_base64(cloth_img)

        # --- Gemini prompt ---
        prompt = """
        Overlay the given clothing item onto the person realistically,
        making it look like they are wearing it. Keep it clean and professional.
        Match body pose and shape.
        """

        # --- Call Gemini ---
        response = client.models.generate_content(
            model="gemini-2.5-flash-image-preview",
            contents=[
                {"type": "image_base64", "image_base64": cloth_img_b64},
                {"type": "image_base64", "image_base64": user_img_b64},
                {"type": "text", "text": prompt}
            ],
        )

        # --- Extract result image ---
        parts = response.candidates[0].content.parts
        result_b64 = next((p.inline_data.data for p in parts if p.inline_data), None)

        if not result_b64:
            return jsonify({"status": "error", "message": "No image generated"}), 400

        result_img = Image.open(BytesIO(base64.b64decode(result_b64)))

        # --- Save to Supabase Storage ---
        file_name = f"tryon_results/{user_id}_{product_id}.png"
        buf = BytesIO()
        result_img.save(buf, format="PNG")
        buf.seek(0)

        # Upload to Supabase bucket "images"
        supabase.storage.from_("images").upload(file_name, buf, {"upsert": True})

        result_url = f"{SUPABASE_URL}/storage/v1/object/public/images/{file_name}"

        # --- Save metadata in DB ---
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
