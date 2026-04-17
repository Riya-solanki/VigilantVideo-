import os
import json
import redis
import boto3
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

# 2. Connect to Cloudflare R2
s3 = boto3.client(
    's3',
    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
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
            
            r.set(f"status:{task_id}", "processing")
            print(f"\n📥 Pulled Task {task_id}. Downloading from R2...")
            
            local_input = f"/kaggle/working/input_{task_id}.mp4"
            local_output = f"/kaggle/working/protected_{task_id}.mp4"
            
            try:
                # 4. Download from Cloudflare R2
                s3.download_file(BUCKET_NAME, raw_object, local_input)
                
                # 5. Execute your optimized GPU pipeline
                print(f"⚙️ Running GPU pipeline...")
                protect_video_gpu(local_input, local_output, batch_size=16)
                
                # 6. Upload the protected video back to Cloudflare R2
                print(f"📤 Uploading Protected Video {task_id} to R2...")
                s3.upload_file(local_output, BUCKET_NAME, protected_object)
                
                # 7. CRITICAL PRIVACY FIX: Delete the raw, unprotected video
                print(f"🗑️ Deleting raw video {task_id} from R2 for privacy...")
                s3.delete_object(Bucket=BUCKET_NAME, Key=raw_object)
                
                r.set(f"status:{task_id}", "completed")
                
            except Exception as e:
                print(f"❌ Error processing task {task_id}: {e}")
                r.set(f"status:{task_id}", "failed")
                
            finally:
                # Clean up local Kaggle disk space
                if os.path.exists(local_input): os.remove(local_input)
                if os.path.exists(local_output): os.remove(local_output)
                print(f"✅ Task {task_id} complete. Waiting for next...")

if __name__ == "__main__":
    worker_loop()