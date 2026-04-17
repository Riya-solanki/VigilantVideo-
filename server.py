from fastapi import FastAPI, UploadFile, File
import boto3
import redis
import uuid
import os
import json
from dotenv import load_dotenv  # Add this import

# Add this line to load the variables from the .env file
load_dotenv()
app = FastAPI()

# ==========================================
# SECURE CONFIGURATION (Environment Variables)
# ==========================================
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
BUCKET_NAME = "vigilant-video-bucket"

# 1. Connect to Upstash Redis
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# 2. Connect to Cloudflare R2
s3 = boto3.client(
    's3',
    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

@app.post("/protect-video/")
async def protect_video(file: UploadFile = File(...)):
    # Generate a unique ID for this processing task
    task_id = str(uuid.uuid4())
    raw_object_name = f"raw/{task_id}.mp4"
    
    # 3. Save the uploaded file temporarily
    temp_path = f"/tmp/{task_id}.mp4"
    with open(temp_path, "wb") as f:
        f.write(await file.read())
        
    # 4. Upload raw video to Cloudflare R2
    s3.upload_file(temp_path, BUCKET_NAME, raw_object_name)
    os.remove(temp_path) # Clean up local server storage
    
    # 5. Push task to the Redis Queue
    task_data = {
        "task_id": task_id,
        "raw_object": raw_object_name,
        "protected_object": f"protected/{task_id}.mp4"
    }
    r.rpush("vigilant_video_queue", json.dumps(task_data))
    r.set(f"status:{task_id}", "queued")
    
    return {"message": "Video uploaded successfully. Processing initiated.", "task_id": task_id}

@app.get("/status/{task_id}")
def check_status(task_id: str):
    status = r.get(f"status:{task_id}")
    return {"task_id": task_id, "status": status}

@app.get("/download/{task_id}")
def get_download_link(task_id: str):
    """Generates a secure, expiring download link for the protected video."""
    status = r.get(f"status:{task_id}")
    
    if status != "completed":
        return {"error": f"Video is not ready yet. Current status: {status}"}
        
    protected_object = f"protected/{task_id}.mp4"
    
    try:
        # Generate a secure download link that expires in 1 hour (3600 seconds)
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': protected_object
            },
            ExpiresIn=3600
        )
        return {"task_id": task_id, "download_url": presigned_url}
        
    except Exception as e:
        return {"error": "Could not generate download link."}