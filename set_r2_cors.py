# -*- coding: utf-8 -*-
"""
set_r2_cors.py -- Apply CORS rules to the Cloudflare R2 bucket.

Run this ONCE (or whenever you update your allowed origins):
    python set_r2_cors.py

Why this is needed:
  The browser uploads video files directly to R2 via a presigned POST URL.
  R2 blocks cross-origin requests unless the bucket CORS policy explicitly
  allows the origin, methods, and headers used by the browser.

  Without this you get in the browser console:
    Access to XMLHttpRequest at 'https://<account>.r2.cloudflarestorage.com/...'
    from origin 'http://127.0.0.1:5000' has been blocked by CORS policy.
"""

import os
import sys
import pathlib

# ── Load .env so credentials are available when running standalone ────
# We do this manually (no python-dotenv dependency needed).
def _load_dotenv(path):
    if not path.exists():
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # Only set if not already present in environment
            os.environ.setdefault(key, val)

_load_dotenv(pathlib.Path(__file__).parent / '.env')

# ── Read R2 credentials from environment ─────────────────────────────
R2_ACCOUNT_ID  = os.environ.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY  = os.environ.get('R2_ACCESS_KEY')
R2_SECRET_KEY  = os.environ.get('R2_SECRET_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'vigilant-video-bucket')

# ── CORS rules ────────────────────────────────────────────────────────
# Add every origin that needs to upload directly to R2.
CORS_RULES = [
    {
        "AllowedOrigins": [
            "http://localhost:5000",
            "http://127.0.0.1:5000",
            "http://localhost:3000",
            "https://vigilantvideo.onrender.com"
            # ---- ADD YOUR PRODUCTION DOMAIN(S) HERE -----------------
            # "https://your-app.onrender.com",
            # "https://www.yourdomain.com",
        ],
        "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
        "AllowedHeaders": [
            # Standard headers
            "Content-Type",
            "Content-Length",
            "Authorization",
            # Presigned POST signature headers (REQUIRED — missing these blocks uploads)
            "x-amz-date",
            "x-amz-content-sha256",
            "x-amz-security-token",
            "x-amz-algorithm",
            "x-amz-credential",
            "x-amz-signature",
        ],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }
]


def main():
    # Validate credentials before attempting connection
    missing = [k for k, v in {
        'R2_ACCOUNT_ID': R2_ACCOUNT_ID,
        'R2_ACCESS_KEY':  R2_ACCESS_KEY,
        'R2_SECRET_KEY':  R2_SECRET_KEY,
    }.items() if not v]

    if missing:
        print("[ERROR] Missing R2 credentials: " + ", ".join(missing))
        print("")
        print("Make sure your .env file contains:")
        for k in missing:
            print(f"  {k}=your-value-here")
        sys.exit(1)

    import boto3
    from botocore.client import Config

    print(f"Connecting to R2 account: {R2_ACCOUNT_ID}")
    print(f"Bucket                  : {R2_BUCKET_NAME}")
    print("")

    s3 = boto3.client(
        's3',
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4'),
    )

    try:
        s3.put_bucket_cors(
            Bucket=R2_BUCKET_NAME,
            CORSConfiguration={"CORSRules": CORS_RULES},
        )
        print("[OK] CORS policy applied successfully!")
        print("")
        print("Allowed origins:")
        for origin in CORS_RULES[0]["AllowedOrigins"]:
            print(f"  - {origin}")
        print("")
        print("Allowed methods: " + ", ".join(CORS_RULES[0]["AllowedMethods"]))
        print("")
        print("The browser can now upload files directly to R2 from these origins.")

    except Exception as e:
        print(f"[ERROR] Failed to apply CORS policy: {e}")
        print("")
        print("Troubleshooting:")
        print("  1. Make sure your R2 API token has Edit permissions on the bucket.")
        print("  2. Verify R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY in your .env.")
        sys.exit(1)


if __name__ == "__main__":
    main()
