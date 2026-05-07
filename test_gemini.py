import os
from google import genai

print("Initializing Gemini Client via Vertex AI...")
try:
    client = genai.Client(vertexai=True, project="glm5-opensource-1775798299", location="us-central1")
    models = ["gemini-3.1-pro", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro"]
    
    success = False
    for m in models:
        try:
            print(f"Testing {m}...")
            response = client.models.generate_content(
                model=m,
                contents="Say hello!"
            )
            print(f"✅ Success with {m}: {response.text.strip()}")
            success = True
            break
        except Exception as e:
            pass
            
    if not success:
        print("❌ All model tests failed.")
except Exception as e:
    print(f"Failed to load client: {e}")
