import torch
import torch.nn.functional as F
import numpy as np

# Create the 8x8 basis matrices once
_dct_basis_8x8 = None
_jpeg_q_table = None

def _init_bases(device):
    global _dct_basis_8x8, _jpeg_q_table
    if _dct_basis_8x8 is None or _dct_basis_8x8.device.type != device.type:
        from scipy.fft import dct
        basis = np.zeros((8, 8))
        for i in range(8):
            v = np.zeros(8)
            v[i] = 1.0
            basis[:, i] = dct(v, type=2, norm='ortho')
        _dct_basis_8x8 = torch.tensor(basis, dtype=torch.float32, device=device)
        
        q_table = np.array([
            [16, 11, 10, 16,  24,  40,  51,  61],
            [12, 12, 14, 19,  26,  58,  60,  55],
            [14, 13, 16, 24,  40,  57,  69,  56],
            [14, 17, 22, 29,  51,  87,  80,  62],
            [18, 22, 37, 56,  68, 109, 103,  77],
            [24, 35, 55, 64,  81, 104, 113,  92],
            [49, 64, 78, 87, 103, 121, 120, 101],
            [72, 92, 95, 98, 112, 100, 103,  99],
        ], dtype=np.float32)
        _jpeg_q_table = torch.tensor(q_table, device=device)

def batched_dct2_8x8(blocks):
    """
    Applies 2D DCT-II to a batch of 8x8 blocks.
    blocks shape: (B, C, H_blocks, W_blocks, 8, 8)
    """
    basis = _dct_basis_8x8
    # DCT = basis @ blocks @ basis.T
    res = torch.matmul(basis, blocks)
    res = torch.matmul(res, basis.t())
    return res

def batched_idct2_8x8(dct_blocks):
    """
    Applies 2D Inverse DCT-II to a batch of 8x8 blocks.
    dct_blocks shape: (B, C, H_blocks, W_blocks, 8, 8)
    """
    basis_t = _dct_basis_8x8.t()
    basis = _dct_basis_8x8
    res = torch.matmul(basis_t, dct_blocks)
    res = torch.matmul(res, basis)
    return res

def apply_frequency_perturbation_gpu(frames_tensor, strength=0.02, jpeg_resilient=True):
    """
    Apply DCT frequency perturbation directly on GPU PyTorch tensors.
    frames_tensor: (B, C, H, W) normalized float tensor in [0, 1]
    """
    device = frames_tensor.device
    _init_bases(device)
    
    B, C, H, W = frames_tensor.shape
    H8, W8 = (H // 8) * 8, (W // 8) * 8
    
    # Scale to 0-255 for standard noise ratios
    cropped = frames_tensor[:, :, :H8, :W8] * 255.0
    
    # Reshape to 8x8 blocks: (B, C, H/8, W/8, 8, 8)
    blocks = cropped.view(B, C, H8//8, 8, W8//8, 8).permute(0, 1, 2, 4, 3, 5)
    
    # Batched DCT
    dct_blocks = batched_dct2_8x8(blocks)
    
    # Generate Noise
    noise = torch.randn_like(dct_blocks) * strength * 255.0
    
    # Frequency Mask
    freq_mask = torch.zeros((8, 8), device=device)
    if jpeg_resilient:
        freq_mask[1:5, 1:5] = 1.0
        q_scale = _jpeg_q_table / _jpeg_q_table.max()
        noise = noise * (1.0 + q_scale)
    else:
        freq_mask[2:7, 2:7] = 1.0
    freq_mask[0, 0] = 0.0
    
    dct_blocks = dct_blocks + (noise * freq_mask)
    
    # Batched IDCT
    idct_blocks = batched_idct2_8x8(dct_blocks)
    
    # Reconstruct Image
    recon = idct_blocks.permute(0, 1, 2, 4, 3, 5).contiguous().view(B, C, H8, W8) / 255.0
    
    res_frames = frames_tensor.clone()
    res_frames[:, :, :H8, :W8] = torch.clamp(recon, 0.0, 1.0)
    return res_frames

def apply_temporal_perturbation_gpu(frames_tensor, prev_batch_last_frame=None, strength=0.015):
    """
    Apply temporal perturbation using fast frame-differencing on GPU.
    frames_tensor: (B, C, H, W) normalized [0,1]
    prev_batch_last_frame: (1, C, H, W) the last frame of the previous batch
    """
    B, C, H, W = frames_tensor.shape
    grayscale = frames_tensor.mean(dim=1, keepdim=True) # (B, 1, H, W)
    
    diffs = torch.zeros_like(grayscale)
    if B > 1:
        diffs[1:] = torch.abs(grayscale[1:] - grayscale[:-1])
    
    if prev_batch_last_frame is not None:
        prev_gray = prev_batch_last_frame.mean(dim=1, keepdim=True)
        diffs[0] = torch.abs(grayscale[0:1] - prev_gray)
    else:
        if B > 1:
            diffs[0] = diffs[1]
            
    # Normalize motion mask
    max_diff = diffs.view(B, -1).max(dim=1)[0].view(B, 1, 1, 1) + 1e-6
    motion_mask = diffs / max_diff
    
    # Apply noise weighted by motion
    noise = torch.randn_like(frames_tensor) * strength
    weighted_noise = noise * (0.3 + 0.7 * motion_mask)
    
    perturbed = torch.clamp(frames_tensor + weighted_noise, 0.0, 1.0)
    return perturbed

def embed_watermark_dct_gpu(frames_tensor, watermark_text="VideoShield", strength=0.1):
    """
    Embed text watermark into DCT blocks on GPU.
    Employs the blue/first channel as the base.
    frames_tensor: (B, C, H, W) normalized [0,1]
    """
    device = frames_tensor.device
    _init_bases(device)
    
    B, C, H, W = frames_tensor.shape
    H8, W8 = (H // 8) * 8, (W // 8) * 8
    
    # Convert text to bits
    bits = []
    for char in watermark_text:
        byte = format(ord(char), "08b")
        bits.extend([int(b) for b in byte])
    if not bits: return frames_tensor
    
    bit_tensor = torch.tensor(bits, dtype=torch.float32, device=device)
    num_bits = len(bits)
    
    q_step = max(strength * 100, _jpeg_q_table[4, 3].item() * 1.5)
    
    # Process only the first channel (channel 0) for watermarking to keep it fast
    c0 = frames_tensor[:, 0:1, :H8, :W8] * 255.0
    blocks = c0.view(B, 1, H8//8, 8, W8//8, 8).permute(0, 1, 2, 4, 3, 5) # (B, 1, H/8, W/8, 8, 8)
    
    dct_blocks = batched_dct2_8x8(blocks)
    
    total_blocks = (H8//8) * (W8//8)
    repeats = (total_blocks // num_bits) + 1
    tiled_bits = bit_tensor.repeat(repeats)[:total_blocks]
    tiled_bits = tiled_bits.view(1, 1, H8//8, W8//8).repeat(B, 1, 1, 1) # (B, 1, H/8, W/8)
    
    # Quantize and modify block at index [4, 3]
    val = dct_blocks[:, :, :, :, 4, 3]
    quantized = torch.round(val / q_step) * q_step
    
    mask_1 = (tiled_bits == 1)
    mask_0 = (tiled_bits == 0)
    
    new_val = quantized.clone()
    new_val[mask_1] += (q_step / 4.0)
    new_val[mask_0] -= (q_step / 4.0)
    
    dct_blocks[:, :, :, :, 4, 3] = new_val
    
    idct_blocks = batched_idct2_8x8(dct_blocks)
    recon = idct_blocks.permute(0, 1, 2, 4, 3, 5).contiguous().view(B, 1, H8, W8) / 255.0
    
    res_frames = frames_tensor.clone()
    res_frames[:, 0:1, :H8, :W8] = torch.clamp(recon, 0.0, 1.0)
    return res_frames
