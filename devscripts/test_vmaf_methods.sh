#!/bin/bash
# 测试三种 VMAF 比较方法
# 使用 proxy 视频，10秒片段
set -euo pipefail

cd "$(dirname "$0")"

REF="Electric_Angel_ref_proxy.mp4"
DIST="BV1Q6W5eLEye-AVC_proxy.mp4"

echo "Reference: $REF"
echo "Distorted: $DIST"
echo "================================"

# --- Method 1: 原始 vmaf_compare.sh 写法 ---
echo ""
echo "[Method 1] 原始写法 (setpts=PTS-STARTPTS)"
ffmpeg -y -i "$DIST" -i "$REF" \
    -lavfi "[0:v]setpts=PTS-STARTPTS[distorted];\
            [1:v]setpts=PTS-STARTPTS[reference];\
            [distorted][reference]libvmaf=log_fmt=json:log_path=method1_vmaf.json" \
    -f null - 2>&1 | tail -3

# --- Method 2: settb=AVTB + format=yuv420p ---
echo ""
echo "[Method 2] settb=AVTB + format=yuv420p"
ffmpeg -y -i "$DIST" -i "$REF" \
    -lavfi "[0:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[dist];\
            [1:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[ref];\
            [dist][ref]libvmaf=log_fmt=json:log_path=method2_vmaf.json" \
    -f null - 2>&1 | tail -3

# --- Method 3: ts_sync_mode=nearest + psnr/ssim features ---
echo ""
echo "[Method 3] settb + ts_sync_mode=nearest + psnr|float_ssim"
ffmpeg -y -i "$DIST" -i "$REF" \
    -lavfi "[0:v]setpts=PTS-STARTPTS[dist];\
            [1:v]setpts=PTS-STARTPTS[ref];\
            [dist][ref]libvmaf=log_fmt=json:log_path=method3_vmaf.json:ts_sync_mode=nearest:feature=name=psnr\|name=float_ssim" \
    -f null - 2>&1 | tail -3

# --- Method 4: 仅强制 60fps PTS，不用 nearest ---
echo ""
echo "[Method 4] force 60fps PTS only (no nearest) + psnr|ssim|ms-ssim|cambi"
ffmpeg -y -i "$DIST" -i "$REF" \
    -lavfi "[0:v]format=yuv420p,settb=AVTB,setpts=N/(60*TB)[dist];\
            [1:v]format=yuv420p,settb=AVTB,setpts=N/(60*TB)[ref];\
            [dist][ref]libvmaf=log_fmt=json:log_path=method4_vmaf.json:feature=name=psnr\|name=float_ssim\|name=float_ms_ssim\|name=cambi:n_threads=8" \
    -f null - 2>&1 | tail -3

# --- 汇总对比 ---
echo ""
echo "================================"
echo "汇总对比"
echo "================================"

python -c "
import json

methods = {
    'Method 1 (original)':        'method1_vmaf.json',
    'Method 2 (settb+format)':    'method2_vmaf.json',
    'Method 3 (nearest+psnr)':    'method3_vmaf.json',
    'Method 4 (60fps PTS)':       'method4_vmaf.json',
}

for name, path in methods.items():
    data = json.loads(open(path).read())
    pooled = data['pooled_metrics']
    vmaf = pooled['vmaf']
    zero_count = sum(1 for f in data['frames'] if f['metrics']['vmaf'] == 0)
    low_count = sum(1 for f in data['frames'] if 0 < f['metrics']['vmaf'] < 50)

    print(f'\n=== {name} ===')
    print(f'  VMAF mean:            {vmaf[\"mean\"]:.2f}')
    print(f'  VMAF harmonic_mean:   {vmaf[\"harmonic_mean\"]:.2f}')
    print(f'  VMAF min/max:         {vmaf[\"min\"]:.2f} / {vmaf[\"max\"]:.2f}')
    print(f'  Zero-VMAF frames:     {zero_count}')
    print(f'  Low-VMAF (<50) frames:{low_count}')

    if 'psnr' in pooled:
        psnr = pooled['psnr']
        print(f'  PSNR mean:            {psnr[\"mean\"]:.2f} dB')
    if 'float_ssim' in pooled:
        ssim = pooled['float_ssim']
        print(f'  SSIM mean:            {ssim[\"mean\"]:.4f}')

    # Show frames 290-310
    frames = {f['frameNum']: f['metrics'] for f in data['frames']}
    print(f'  Frames 290-310:')
    print(f'    {\"frame\":>5} {\"vmaf\":>8} {\"adm2\":>8} {\"vif_s0\":>8} {\"motion\":>8}')
    for fn in range(290, 311):
        m = frames.get(fn, {})
        v = m.get('vmaf', float('nan'))
        adm2 = m.get('integer_adm2', float('nan'))
        vif0 = m.get('integer_vif_scale0', float('nan'))
        motion = m.get('integer_motion', float('nan'))
        marker = ' <-- ZERO' if v == 0 else ''
        print(f'    {fn:5d} {v:8.2f} {adm2:8.4f} {vif0:8.4f} {motion:8.4f}{marker}')
"

echo ""
echo "完成！JSON 文件: method1_vmaf.json, method2_vmaf.json, method3_vmaf.json"
