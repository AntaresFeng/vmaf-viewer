from __future__ import annotations

import argparse
from pathlib import Path

from vmaf_workflow.watermark_detection import (
    DetectionSettings,
    detect_watermark,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="比较变质视频与干净参考视频，探索固定水印检测；不读写 workflow 状态。",
        add_help=False,
    )
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")
    parser.add_argument(
        "-D",
        "--distorted",
        type=Path,
        required=True,
        default=argparse.SUPPRESS,
        metavar="视频",
        help="待检测的变质视频，通常是带水印的 B 站编码视频",
    )
    parser.add_argument(
        "-R",
        "--reference",
        type=Path,
        required=True,
        default=argparse.SUPPRESS,
        metavar="视频",
        help="与变质视频内容和时间线一致的干净参考视频",
    )
    parser.add_argument(
        "-O",
        "--output-dir",
        type=Path,
        required=True,
        default=argparse.SUPPRESS,
        metavar="目录",
        help="诊断图片和 summary.json 的输出目录；不存在时自动创建",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=9,
        metavar="数量",
        help="在共同视频时长的 8%%～92%% 之间均匀抽取的帧数，至少为 3（默认：9）",
    )
    parser.add_argument(
        "--analysis-width",
        type=int,
        default=960,
        metavar="像素",
        help="分析帧的缩放宽度，高度按变质视频宽高比计算，至少为 64（默认：960）",
    )
    parser.add_argument(
        "--edge-ratio",
        type=float,
        default=0.24,
        metavar="比例",
        help="画面四周参与搜索的边缘带比例，范围为 0.05～0.45（默认：0.24）",
    )
    parser.add_argument(
        "--minimum-frequency",
        type=float,
        default=0.56,
        metavar="比例",
        help="像素达到残差阈值所需的最低采样帧比例，建议范围为 0～1（默认：0.56）",
    )
    parser.add_argument(
        "--minimum-median-z",
        type=float,
        default=3.0,
        metavar="Z值",
        help="持续正向残差需要达到的最低稳健 Z 值；越高越严格（默认：3.0）",
    )
    parser.add_argument(
        "--sync-radius-frames",
        type=int,
        default=1,
        metavar="帧数",
        help="每个采样点在参考视频前后搜索的同步帧数，不得为负数（默认：1）",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        metavar="命令或路径",
        help="用于解码和缩放采样帧的 FFmpeg 可执行文件（默认：ffmpeg）",
    )
    parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        metavar="命令或路径",
        help="用于读取分辨率、帧率和时长的 ffprobe 可执行文件（默认：ffprobe）",
    )
    return parser


def analyze(args: argparse.Namespace) -> dict:
    settings = DetectionSettings(
        samples=args.samples,
        analysis_width=args.analysis_width,
        edge_ratio=args.edge_ratio,
        minimum_frequency=args.minimum_frequency,
        minimum_median_z=args.minimum_median_z,
        sync_radius_frames=args.sync_radius_frames,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )
    return detect_watermark(
        args.distorted,
        args.reference,
        args.output_dir,
        settings,
    ).to_summary()


def main() -> None:
    args = build_parser().parse_args()
    summary = analyze(args)
    print(f"Wrote watermark research artifacts to {args.output_dir.resolve()}")
    candidates = summary["candidates"]
    if candidates:
        candidate = candidates[0]
        print(
            "Top candidate: "
            f"{candidate['corner']} "
            f"bbox=({candidate['x']},{candidate['y']},"
            f"{candidate['width']},{candidate['height']}) "
            f"score={candidate['score']:.3f}"
        )
    else:
        print("No candidate passed the current thresholds")


if __name__ == "__main__":
    main()
