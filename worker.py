
import os
import json
import redis
import boto3
from botocore.client import Config  # <-- NEW: Added for R2 compatibility
import requests
from protection_gpu_v2 import protect_video_gpu

# ==========================================
# SECURE CONFIGURATION (Environment Variables)
# ==========================================
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
BUCKET_NAME = "vigilant-video-bucket"
QUEUE_NAME = "vigilant_video_queue"

# 1. Connect to Upstash Redis
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# 2. Connect to Cloudflare R2 (with strict s3v4 signature)
s3 = boto3.client(
    's3',
    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    config=Config(signature_version='s3v4') # <-- FIX 2
)

print("🚀 Kaggle Worker Node Online. Polling for tasks...")

def worker_loop():
    while True:
        # 3. Block and wait for the next task
        task = r.blpop(QUEUE_NAME, timeout=0) 
        
        if task:
            _, message_data = task
            job = json.loads(message_data)
            task_id = job['task_id']
            raw_object = job['raw_object']
            protected_object = job['protected_object']
            
            webhook_url = job.get('webhook_url')
            webhook_secret = job.get('webhook_secret')
            
            r.set(f"status:{task_id}", "processing")
            print(f"\n📥 Pulled Task {task_id}. Downloading from R2...")
            
            local_input = f"/kaggle/working/input_{task_id}.mp4"
            local_output = f"/kaggle/working/protected_{task_id}.mp4"
            
            try:
                # 4. Download from Cloudflare R2
                s3.download_file(BUCKET_NAME, raw_object, local_input)
                
                # 5. Execute your optimized GPU pipeline and capture metrics
                print(f"⚙️ Running GPU pipeline...")
                metrics = protect_video_gpu(local_input, local_output, batch_size=16)
                
                # 6. Upload the protected video back to Cloudflare R2
                print(f"📤 Uploading Protected Video {task_id} to R2...")
                s3.upload_file(local_output, BUCKET_NAME, protected_object)
                
                r.set(f"status:{task_id}", "completed")
                
                # 7. Notify the Flask App that the job is successfully done
                if webhook_url:
                    print(f"🔔 Sending success webhook to {webhook_url}...")
                    payload = {
                        "task_id": task_id,
                        "status": "done",
                        "webhook_secret": webhook_secret,
                        "metrics": metrics
                    }
                    # <-- FIX 3: Added 10 second timeout
                    requests.post(webhook_url, json=payload, timeout=10) 
                
            except Exception as e:
                print(f"❌ Error processing task {task_id}: {e}")
                r.set(f"status:{task_id}", "failed")
                
                # Notify the Flask App that the job failed
                if webhook_url:
                    print(f"🔔 Sending error webhook to {webhook_url}...")
                    payload = {
                        "task_id": task_id,
                        "status": "error",
                        "webhook_secret": webhook_secret,
                        "error_message": str(e)
                    }
                    try:
                        # <-- FIX 3: Added timeout
                        requests.post(webhook_url, json=payload, timeout=10)
                    except requests.exceptions.RequestException as req_err:
                        print(f"⚠️ Webhook failed to deliver error state: {req_err}")
                
            finally:
                # 8. Scorched Earth Cleanup (FIX 1: Moved raw deletion here)
                print(f"🗑️ Running final privacy & disk cleanup...")
                
                # Delete from Cloudflare R2
                try:
                    s3.delete_object(Bucket=BUCKET_NAME, Key=raw_object)
                except Exception as s3_err:
                    print(f"⚠️ Failed to delete raw object from R2: {s3_err}")

                # Clean up local Kaggle disk space
                if os.path.exists(local_input): os.remove(local_input)
                if os.path.exists(local_output): os.remove(local_output)
                
                print(f"✅ Task {task_id} fully resolved. Waiting for next...")

if __name__ == "__main__":
    worker_loop()