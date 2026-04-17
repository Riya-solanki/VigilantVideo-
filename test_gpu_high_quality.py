"""
Kaggle/Colab Test Script for VideoShield GPU (High Quality Setting)
===================================================================
Run this script to protect a video with a focus on visual clarity.
"""

import time
from protection_gpu_v2 import protect_video_gpu

# Set your input video path here
input_video = "test_1min.mp4" 
output_video = "test_1min_hq_protected.mp4"

print(f"🚀 Starting High-Quality GPU Protection Pipeline...")
print(f"Batch Size: 16")

start = time.time()

# Run the protection with reduced noise intensities for better visual quality
result = protect_video_gpu(
    input_video, 
    output_video, 
    batch_size=16,
    model_profile="lite",
    noise_strength=0.015,         # Less visible statics
    freq_strength=0.01,           # Lower frequency blocking
    temp_strength=0.008,          # Lower frame-to-frame flickering
    wm_strength=0.05,             # Less prominent watermark
    watermark_text="VideoShield GPU"
)

end = time.time()

print("\n✅ Protection Complete!")
print(f"Total Time: {result['duration_seconds']} seconds")
print(f"Average FPS: {result['fps']}")
print(f"Average LPIPS: {result['avg_lpips']}")
print(f"Output saved to: {output_video}")
