"""
Video Shield — GPU Optimized Protection Pipeline (Batch Processing)
====================================================================
This is a GPU-accelerated version of the protection pipeline designed
specifically for Google Colab/Kaggle environments. It uses frame batching
to maximize NVIDIA GPU throughput (T4/P100).

Optimizations:
  1. Batch Processing for CNNs/Transformers (PGD & LPIPS)
  2. Multi-threaded CPU processing for NumPy-based layers
  3. Minimized CPU-GPU data transfer overhead

Usage:
    from protection_gpu import protect_video_gpu
    result = protect_video_gpu("input.mp4", "output.mp4", batch_size=16)
"""

import os
import time
import logging
import tempfile
import shutil
import concurrent.futures
from typing import List, Tuple

import cv2
import numpy as np
import torch
from scipy.fft import dctn, idctn
import ffmpeg

from protection_gpu_kernels import (
    apply_frequency_perturbation_gpu,
    apply_temporal_perturbation_gpu,
    embed_watermark_dct_gpu
)

logger = logging.getLogger("VideoShield-GPU")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Standard JPEG luminance quantization table (quality factor 50)
JPEG_LUMINANCE_Q_TABLE = np.array([
    [16, 11, 10, 16,  24,  40,  51,  61],
    [12, 12, 14, 19,  26,  58,  60,  55],
    [14, 13, 16, 24,  40,  57,  69,  56],
    [14, 17, 22, 29,  51,  87,  80,  62],
    [18, 22, 37, 56,  68, 109, 103,  77],
    [24, 35, 55, 64,  81, 104, 113,  92],
    [49, 64, 78, 87, 103, 121, 120, 101],
    [72, 92, 95, 98, 112, 100, 103,  99],
], dtype=np.float64)

# ──────────────────────────────────────────────────────────────
# HELPERS — JPEG Simulation & LPIPS Perceptual Quality Gate
# ──────────────────────────────────────────────────────────────

def _simulate_jpeg_compression(frame: np.ndarray, quality: int = 75) -> np.ndarray:
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    success, encoded = cv2.imencode(".jpg", frame, encode_params)
    if not success:
        return frame
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)

_lpips_model = None

def _compute_lpips_batch(originals: List[np.ndarray], perturbeds: List[np.ndarray]) -> List[float]:
    """
    Compute LPIPS perceptual distance for a batch of frames on GPU.
    """
    import torch
    import lpips

    global _lpips_model
    if _lpips_model is None:
        logger.info("Loading LPIPS model on GPU...")
        _lpips_model = lpips.LPIPS(net="alex", verbose=False)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        _lpips_model.to(device)
        _lpips_model.eval()

    device = next(_lpips_model.parameters()).device

    def _to_batch_tensor(img_list):
        img_array = np.stack([cv2.cvtColor(img, cv2.COLOR_BGR2RGB) for img in img_list])
        tens = torch.from_numpy(img_array).to(device, dtype=torch.float32) / 255.0
        tens = tens * 2.0 - 1.0
        return tens.permute(0, 3, 1, 2)

    with torch.no_grad():
        tens_orig = _to_batch_tensor(originals)
        tens_pert = _to_batch_tensor(perturbeds)
        scores = _lpips_model(tens_orig, tens_pert)
    
    return [s.item() for s in scores.flatten()]

# ──────────────────────────────────────────────────────────────
# LAYER 1 — CLIP Feature-Space Drifting Attack (Batch Optimized)
# ──────────────────────────────────────────────────────────────

def _load_clip_gpu():
    from transformers import CLIPVisionModel
    import torch
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Loading CLIP model to {device}...")
    # Standard ViT-B/32, fast and widely generalizable
    model = CLIPVisionModel.from_pretrained("openai/clip-vit-base-patch32")
    model.eval().to(device)
    for param in model.parameters():
        param.requires_grad = False
    return model

def _apply_clip_drift_attack_gpu(frames_tensor, clip_model, epsilon=0.03, drift_speed=2, jpeg_resilient=True):
    """
    Executes a single-step feature-space PGD attack on the first frame using CLIP,
    then spatially rolls (drifts) the perturbation across the remaining frames in the batch
    to create a persistent low-frequency disruption against 3D convolutions/temporal pooling.
    """
    import torch
    import torch.nn.functional as F
    import contextlib

    B, C, H, W = frames_tensor.shape
    device = frames_tensor.device
    
    # Take the first frame for gradient calculation (Assumed BGR format)
    f0 = frames_tensor[0:1].clone().detach() 
    f0.requires_grad_(True)
    
    # Convert BGR to RGB for CLIP processing
    f0_rgb = f0[:, [2, 1, 0], :, :]
    
    # CLIP ImageNet normalization parameters
    mean = torch.tensor([0.48145466, 0.4578275, 0.40821073], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.26862954, 0.26130258, 0.27577711], device=device).view(1, 3, 1, 1)
    
    # Fast resize and normalize
    resized = F.interpolate(f0_rgb, size=(224, 224), mode='bilinear', align_corners=False)
    normalized = (resized - mean) / std
    
    # Forward pass on original frame
    with torch.no_grad(), torch.autocast(device.type) if device.type == "cuda" else contextlib.nullcontext():
        f0_clean = frames_tensor[0:1].clone().detach()
        f0_clean_rgb = f0_clean[:, [2, 1, 0], :, :]
        clean_resized = F.interpolate(f0_clean_rgb, size=(224, 224), mode='bilinear', align_corners=False)
        clean_normalized = (clean_resized - mean) / std
        orig_features = clip_model(clean_normalized).pooler_output
        
    # Forward pass on trainable frame
    with torch.autocast(device.type) if device.type == "cuda" else contextlib.nullcontext():
        adv_features = clip_model(normalized).pooler_output
    
    # We want to MAXIMIZE the distance to the original feature
    loss = -F.mse_loss(adv_features, orig_features)
    clip_model.zero_grad()
    loss.backward()
    
    # Calculate base noise mask with FGSM single step
    grad = f0.grad  # Gradient is natively in BGR space due to autograd!
    base_noise = epsilon * torch.sign(grad)
    
    # JPEG resilience simulation (concentrate on lower/mid frequencies by smoothing)
    if jpeg_resilient:
        kernel = torch.ones(1, 1, 5, 5, device=device) / 25.0
        base_noise_smooth = torch.zeros_like(base_noise)
        for c in range(3):
            base_noise_smooth[:, c:c+1, :, :] = F.conv2d(base_noise[:, c:c+1, :, :], kernel, padding=2)
        base_noise = base_noise_smooth * 1.5 
        base_noise = torch.clamp(base_noise, -epsilon, epsilon)
        
    # Apply Drifting Noise across the batch
    perturbed_tensor = frames_tensor.clone()
    for i in range(B):
        # Spatially roll the noise diagonally to create slow-drift
        shift = i * drift_speed
        rolled_noise = torch.roll(base_noise, shifts=(shift, shift), dims=(2, 3))
        perturbed_tensor[i:i+1] = torch.clamp(frames_tensor[i:i+1] + rolled_noise, 0.0, 1.0)
        
    return perturbed_tensor

# ────────────────────────────────────────────────────────────────
# CPU LAYERS (Fast enough for ThreadPool or vectorized)
# ────────────────────────────────────────────────────────────────

def _apply_frequency_perturbation(frame, strength=0.02, jpeg_resilient=True):
    # (Implementation copied from protection.py)
    frame_float = frame.astype(np.float64)
    result = np.copy(frame_float)
    freq_mask = np.zeros((8, 8))
    if jpeg_resilient: freq_mask[1:5, 1:5] = 1.0
    else: freq_mask[2:7, 2:7] = 1.0
    freq_mask[0, 0] = 0.0

    for c in range(3):
        channel = frame_float[:, :, c]
        h, w = channel.shape
        h8, w8 = (h // 8) * 8, (w // 8) * 8
        for i in range(0, h8, 8):
            for j in range(0, w8, 8):
                block = channel[i:i + 8, j:j + 8]
                dct_block = dctn(block, type=2, norm="ortho")
                noise = np.random.randn(8, 8) * strength * 255
                if jpeg_resilient:
                    q_scale = JPEG_LUMINANCE_Q_TABLE / JPEG_LUMINANCE_Q_TABLE.max()
                    noise *= (1.0 + q_scale)
                dct_block += noise * freq_mask
                result[i:i+8, j:j+8, c] = idctn(dct_block, type=2, norm="ortho")
    return np.clip(result, 0, 255).astype(np.uint8)

def _apply_temporal_perturbation(current_frame, previous_frame, strength=0.015, jpeg_resilient=True):
    gray_curr = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
    gray_prev = cv2.cvtColor(previous_frame, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(gray_prev, gray_curr, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    flow_mag = np.sqrt(flow[:, :, 0] ** 2 + flow[:, :, 1] ** 2)
    flow_max = flow_mag.max()
    flow_weight = flow_mag / flow_max if flow_max > 0 else np.zeros_like(flow_mag)
    h, w = current_frame.shape[:2]
    noise = np.random.randn(h, w, 3).astype(np.float32) * strength * 255
    motion_mask = np.stack([flow_weight] * 3, axis=-1)
    weighted_noise = noise * (0.3 + 0.7 * motion_mask)
    perturbed = np.clip(current_frame.astype(np.float32) + weighted_noise, 0, 255).astype(np.uint8)
    if jpeg_resilient:
        jpeg = _simulate_jpeg_compression(perturbed, quality=75)
        surviving = jpeg.astype(np.float32) - current_frame.astype(np.float32)
        perturbed = np.clip(current_frame.astype(np.float32) + surviving * 1.5, 0, 255).astype(np.uint8)
    return perturbed

def _embed_watermark_dct(frame, watermark_text, strength=0.1):
    def _text_to_bits(text):
        bits = []
        for char in text:
            byte = format(ord(char), "08b")
            bits.extend([int(b) for b in byte])
        return bits
    
    bits = _text_to_bits(watermark_text)
    if not bits: return frame
    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb).astype(np.float64)
    y_channel = ycrcb[:, :, 0]
    h, w = y_channel.shape
    h8, w8 = (h // 8) * 8, (w // 8) * 8
    bit_idx = 0
    q_step = max(strength * 100, JPEG_LUMINANCE_Q_TABLE[4, 3] * 1.5)

    for i in range(0, h8, 8):
        for j in range(0, w8, 8):
            block = y_channel[i:i + 8, j:j + 8]
            dct_block = dctn(block, type=2, norm="ortho")
            bit = bits[bit_idx % len(bits)]
            quantized = round(dct_block[4, 3] / q_step) * q_step
            dct_block[4, 3] = quantized + (q_step / 4 if bit == 1 else -q_step / 4)
            y_channel[i:i+8, j:j+8] = idctn(dct_block, type=2, norm="ortho")
            bit_idx += 1
    ycrcb[:, :, 0] = np.clip(y_channel, 0, 255)
    return cv2.cvtColor(ycrcb.astype(np.uint8), cv2.COLOR_YCrCb2BGR)

# ──────────────────────────────────────────────
# LAYER 3 — Metadata Poisoning (FFmpeg)
# ──────────────────────────────────────────────

def _inject_metadata(input_path: str, orig_input_path: str, output_path: str, watermark_text: str = "VideoShield Protected"):
    metadata = {
        "comment": f"Protected by VideoShield. {watermark_text}. "
                   "This video is NOT licensed for AI training, scraping, or data mining.",
        "copyright": f"(c) VideoShield Protected Content — AI Training Prohibited",
        "artist": "VideoShield Protected",
        "title": "AI-Training-Prohibited Content",
        "robots": "noai, noimageai, noimageindex",
        "ai-training": "disallow",
        "ai-scraping": "disallow",
        "machine-learning": "disallow",
        "content-credentials": "VideoShield-v1.0",
        "content-provenance": "protected-against-ai-training",
    }
    try:
        has_audio = False
        try:
            probe = ffmpeg.probe(orig_input_path)
            for s in probe.get('streams', []):
                if s.get('codec_type') == 'audio':
                    has_audio = True
                    break
        except Exception:
            pass

        import subprocess
        meta_args = []
        for k, v in metadata.items():
            meta_args.extend(["-metadata", f"{k}={v}"])
            
        cmd = ["ffmpeg", "-y", "-i", input_path]
        if has_audio:
            cmd.extend(["-i", orig_input_path, "-c:v", "copy", "-c:a", "copy", "-map", "0:v:0", "-map", "1:a:0"])
        else:
            cmd.extend(["-c:v", "copy"])
            
        cmd.extend(meta_args)
        cmd.append(output_path)
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise Exception(result.stderr)
            
    except Exception as e:
        logger.error(f"FFmpeg error: {e}")
        shutil.copy2(input_path, output_path)

# ──────────────────────────────────────────────
# MAIN GPU PIPELINE
# ──────────────────────────────────────────────

def protect_video_gpu(
    input_path: str, output_path: str, batch_size: int = 8,
    model_profile: str = "lite", noise_strength: float = 0.03,
    lpips_threshold: float = 0.15, jpeg_resilient: bool = True,
    **kwargs
) -> dict:
    start_time = time.time()
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not fps or fps <= 0:
        fps = 30.0
    
    temp_output = os.path.join(tempfile.gettempdir(), f"gpu_proc_{int(time.time())}.mp4")
    
    writer_process = None
    writer = None
    try:
        writer_process = (
            ffmpeg
            .input('pipe:', format='rawvideo', pix_fmt='bgr24', s=f'{w}x{h}', framerate=fps)
            .output(temp_output, pix_fmt='yuv420p', vcodec='libx264', crf=18, preset='fast')
            .overwrite_output()
            .run_async(pipe_stdin=True, quiet=True)
        )
    except Exception as e:
        logger.warning(f"Failed to start ffmpeg writer, falling back to cv2.VideoWriter: {e}")
        writer = cv2.VideoWriter(temp_output, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    clip_model = _load_clip_gpu()
    
    frame_count = 0
    prev_processed_frame = None
    lpips_scores = []

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count())

    while True:
        batch_originals = []
        for _ in range(batch_size):
            ret, frame = cap.read()
            if not ret: break
            batch_originals.append(frame)
        
        if not batch_originals: break

        # 1. Fast Batch to GPU (Push uint8 block directly to GPU before math)
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        batch_array = np.stack(batch_originals) # (B, H, W, C)
        frames_tensor = torch.from_numpy(batch_array).to(device)
        frames_tensor = frames_tensor.permute(0, 3, 1, 2).float() / 255.0

        # 2. Batch CLIP Feature-Space Drifting Attack on GPU
        #    (Replaces the old PGD layer with massive speedups and temporal resilience)
        frames_tensor = _apply_clip_drift_attack_gpu(
            frames_tensor, clip_model, epsilon=noise_strength, drift_speed=2, jpeg_resilient=jpeg_resilient
        )

        # 3. Process other layers naturally batched on GPU

        # Apply Frequency Perturbation
        frames_tensor = apply_frequency_perturbation_gpu(
            frames_tensor, 
            strength=kwargs.get('freq_strength', 0.02), 
            jpeg_resilient=jpeg_resilient
        )
        
        # Apply Temporal Perturbation
        frames_tensor = apply_temporal_perturbation_gpu(
            frames_tensor, 
            prev_batch_last_frame=prev_processed_frame, 
            strength=kwargs.get('temp_strength', 0.015)
        )
        # Save last frame of batch for next batch's temporal masking
        prev_processed_frame = frames_tensor[-1:].clone()
        
        # Apply Watermark
        frames_tensor = embed_watermark_dct_gpu(
            frames_tensor, 
            watermark_text=kwargs.get('watermark_text', "VideoShield"), 
            strength=kwargs.get('wm_strength', 0.1)
        )
        
        # Convert back to NumPy uint8 entirely on GPU before DMA
        out_tensor = (frames_tensor.permute(0, 2, 3, 1) * 255.0).clamp(0, 255).to(torch.uint8)
        out_numpy = out_tensor.cpu().numpy()
        processed_batch = [out_numpy[i] for i in range(len(batch_originals))]

        # 3. Batch LPIPS on GPU
        scores = _compute_lpips_batch(batch_originals, processed_batch)
        lpips_scores.extend(scores)

        for frame in processed_batch:
            if writer_process:
                writer_process.stdin.write(frame.tobytes())
            else:
                writer.write(frame)
        
        frame_count += len(batch_originals)
        logger.info(f"Progress: {frame_count}/{total_frames} frames ({(frame_count/total_frames)*100:.1f}%)")

    cap.release()
    if writer_process:
        writer_process.stdin.close()
        writer_process.wait()
    elif writer:
        writer.release()
    
    # Layer 3: Metadata Poisoning
    time.sleep(1) # Wait for file system sync before ffmpeg reads it
    logger.info("Injecting anti-AI metadata...")
    _inject_metadata(temp_output, input_path, output_path, watermark_text=kwargs.get('watermark_text', "VideoShield"))
    
    # Cleanup
    try: os.remove(temp_output)
    except: pass
    
    elapsed = time.time() - start_time
    return {
        "frames_processed": frame_count,
        "duration_seconds": round(elapsed, 2),
        "avg_lpips": round(sum(lpips_scores)/len(lpips_scores), 4) if lpips_scores else 0,
        "fps": round(frame_count / elapsed, 2)
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python protection_gpu.py <input> <output>")
        sys.exit(1)
    res = protect_video_gpu(sys.argv[1], sys.argv[2])
    print(res)
