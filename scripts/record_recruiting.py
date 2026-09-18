"""Record only an explicitly selected app tab, retaining original CDP timestamps.

No navigation, search, DOM changes, or model calls. SIGINT and SIGTERM finalize
metadata and optionally render the recording at its original speed. CDP captures
the entire visible tab content, not Chrome's toolbar or other windows.
"""

import argparse
import base64
import json
import signal
import subprocess
import time
from pathlib import Path

from browser_harness.helpers import cdp, drain_events


def render(folder: Path, metadata: dict, ffmpeg: str) -> Path:
    frames = [json.loads(line) for line in (folder / "frames.jsonl").read_text().splitlines()]
    if not frames:
        raise ValueError("No recorded frames")
    lines = ["ffconcat version 1.0"]
    for index, frame in enumerate(frames):
        end = frames[index + 1]["elapsed_seconds"] if index + 1 < len(frames) else metadata["duration_seconds"]
        lines.extend(
            [
                f"file '{frame['file']}'",
                "option framerate 1000",
                f"duration {max(0.001, end - frame['elapsed_seconds']):.6f}",
            ]
        )
    lines.append(f"file '{frames[-1]['file']}'")
    manifest = folder / "frames.ffconcat"
    manifest.write_text("\n".join(lines) + "\n")
    output = folder / "demo.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-n",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-t",
            str(metadata["duration_seconds"]),
            "-vf",
            "fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="New recording directory, never overwrites an existing recording")
    parser.add_argument("--target", required=True, help="Exact existing Chrome target ID")
    parser.add_argument("--duration", type=float, default=0, help="Stop after seconds; zero waits for a signal")
    parser.add_argument("--render", action="store_true", help="Render demo.mp4 after capture")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable")
    args = parser.parse_args()
    if args.duration < 0:
        parser.error("duration must be nonnegative")
    info = cdp("Target.getTargetInfo", targetId=args.target)["targetInfo"]
    if info["type"] != "page":
        parser.error("target must be a page")
    folder = args.folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "frames").mkdir()
    session = cdp("Target.attachToTarget", targetId=args.target, flatten=True)["sessionId"]
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    epoch = time.time()
    began = time.monotonic()
    count = 0
    last_elapsed = 0.0
    metadata = {
        "target": args.target,
        "url": info["url"],
        "started_epoch_seconds": epoch,
        "capture": "entire visible tab content",
        "speed": 1,
        "errors": [],
    }
    (folder / "recording.json").write_text(json.dumps(metadata, indent=2))
    stream = (folder / "frames.jsonl").open("w")

    def save(data, timestamp, raw_metadata=None):
        nonlocal count, last_elapsed
        # Retain the raw browser timestamp even if its clock differs from Python.
        elapsed = max(last_elapsed, timestamp - epoch)
        path = f"frames/{count:08d}.jpg"
        (folder / path).write_bytes(base64.b64decode(data))
        stream.write(
            json.dumps(
                {
                    "file": path,
                    "elapsed_seconds": elapsed,
                    "browser_timestamp": timestamp,
                    "metadata": raw_metadata,
                }
            )
            + "\n"
        )
        stream.flush()
        count += 1
        last_elapsed = elapsed

    try:
        initial = cdp("Page.captureScreenshot", session_id=session, format="jpeg", quality=85)
        save(initial["data"], epoch)
        cdp("Page.startScreencast", session_id=session, format="jpeg", quality=85, everyNthFrame=1)
        print(json.dumps({"recording": str(folder), "target": args.target}), flush=True)
        while not stopped and (not args.duration or time.monotonic() - began < args.duration):
            for event in drain_events():
                if event.get("session_id") != session or event.get("method") != "Page.screencastFrame":
                    continue
                params = event["params"]
                try:
                    save(params["data"], params["metadata"]["timestamp"], params["metadata"])
                finally:
                    cdp("Page.screencastFrameAck", session_id=session, sessionId=params["sessionId"])
            time.sleep(0.02)
    except Exception as error:
        metadata["errors"].append(str(error))
    finally:
        metadata["duration_seconds"] = max(last_elapsed, time.monotonic() - began)
        metadata["frame_count"] = count
        for method, params in [
            ("Page.stopScreencast", {"session_id": session}),
            ("Target.detachFromTarget", {"sessionId": session}),
        ]:
            try:
                cdp(method, **params)
            except Exception as error:
                metadata["errors"].append(f"{method}: {error}")
        stream.close()
        (folder / "recording.json").write_text(json.dumps(metadata, indent=2))
    if args.render and count:
        print(render(folder, metadata, args.ffmpeg), flush=True)
    print(json.dumps(metadata), flush=True)
    if metadata["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
