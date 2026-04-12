"""
protection.py — Stub module for video protection
This is a placeholder that returns mock results so the app can run.
Replace with actual video protection logic when ready.
"""

import os
import shutil
import time


def protect_video(input_path, output_path):
    """
    Stub implementation of video protection.
    Copies the original video as the 'protected' output and returns mock metadata.

    Args:
        input_path:  path to the original video file
        output_path: path where the protected video should be saved

    Returns:
        dict with protection metadata
    """
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)

    # For now, just copy the file (replace with real protection logic later)
    if os.path.exists(input_path):
        shutil.copy2(input_path, output_path)

    return {
        'watermark_text': 'VigilantVideo',
        'watermark_strength': 0.1,
        'noise_strength': 0.03,
        'freq_perturbation_strength': 0.02,
        'frames_processed': 100,
        'duration_seconds': 2.5,
        'protections_applied': ['watermark', 'noise_injection'],
        'models_used': ['stub_model_v1'],
    }
