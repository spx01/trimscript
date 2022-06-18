#!/usr/bin/env python3

ffmpeg_exe = "ffmpeg"
ffprobe_exe = "ffprobe"

# container/codec/whatever overhead
# TODO: run tests to find out roughly how this scales and how to approximate it?
overhead = 1000000

# TODO: switch to using click instead of argparse
import argparse
import humanfriendly
from parse import parse
import os
import subprocess
from colorama import Fore
import math
import shutil


def err_print(*args, **kwargs):
    print(Fore.RED, end="")
    print(*args, Fore.RESET, **kwargs)


def info_print(*args, **kwargs):
    if not args[0].startswith("[ffmpeg]") and not quiet:
        print(Fore.YELLOW, *args, Fore.RESET, **kwargs)


def check_exe(exe):
    return os.path.isfile(exe) or shutil.which(exe) is not None


parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
# TODO: accept non integer values for timestamps
parser.add_argument(
    "-f",
    "--start",
    metavar="TIME",
    type=str,
    nargs=1,
    default=["0"],
    help="start timetamp (in H:M:S / M:S / S format)",
)
parser.add_argument(
    "-t",
    "--end",
    metavar="TIME",
    type=str,
    nargs=1,
    default=["end"],
    help="end timetamp (in H:M:S / M:S / S format)",
)
parser.add_argument(
    "-z",
    "--max-size",
    metavar="SIZE",
    type=str,
    nargs=1,
    default=["8MiB"],
    help="max size of output",
)
parser.add_argument(
    "-r",
    "--framerate",
    metavar="FRAMERATE",
    type=int,
    nargs=1,
    default=[0],
    help="framerate to convert to (default is to keep the original)",
)
parser.add_argument(
    "-s",
    "--resolution",
    metavar="RESOLUTION",
    type=str,
    nargs=1,
    default=["keep"],
    help="resolution",
)
parser.add_argument(
    "-o",
    "--output",
    metavar="OUTPUT_PATH",
    type=str,
    nargs=1,
    default=["out.mp4"],
    help="output video path",
)
parser.add_argument(
    "-a",
    "--audio-tracks",
    metavar="TRACKS",
    type=str,
    nargs=1,
    default=["all"],
    help="comma separated audio tracks or all or none",
)
parser.add_argument(
    "--audio-bitrate",
    metavar="BITRATE",
    type=str,
    nargs=1,
    default=["48k"],
    help="audio bitrate to convert to",
)
parser.add_argument(
    "--no-mix-audio",
    action="store_true",
    help="don't mix audio tracks into one (the default behavior)",
)
parser.add_argument(
    "--preset",
    metavar="PRESET",
    type=str,
    nargs=1,
    default=["medium"],
    help="x264 preset to use",
)
parser.add_argument(
    "--cvc", action="store_true", help="don't reencode video, max size will be ignored"
)
parser.add_argument(
    "-q", "--quiet", action="store_true", help="quiet and non interactive mode"
)
parser.add_argument(
    "--dry-run", action="store_true", help="only print the commands, don't execute them"
)
parser.add_argument(
    "input", metavar="INPUT", type=str, nargs=1, help="input video path"
)
args = parser.parse_args()


def parse_timestamp(timestamp):
    if timestamp == "end":
        return -1

    res = parse("{hours:d}:{minutes:d}:{seconds:d}", timestamp)
    if res is None:
        res = parse("{minutes:d}:{seconds:d}", timestamp)
    if res is None:
        res = parse("{seconds:d}", timestamp)
    if res is None:
        err_print("invalid timestamp")
        exit(1)
    hours = res["hours"] if "hours" in res else 0
    minutes = res["minutes"] if "minutes" in res else 0
    return hours * 3600 + minutes * 60 + res["seconds"]


def get_video_duration(video_path):
    if not os.path.isfile(video_path):
        err_print("invalid video path")
        exit(1)

    command = f'"{ffprobe_exe}" -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
    try:
        output = subprocess.check_output(command, shell=True).decode("utf-8")
    except subprocess.CalledProcessError as e:
        err_print(
            f"could not probe file, ffprobe failed with return code {e.returncode}"
        )
        exit(1)
    return float(output)


def parse_audio_tracks(tracks):
    if tracks == "all":
        return None
    if tracks == "none":
        return []
    return [int(x) for x in tracks.split(",")]


def get_audio_track_num(video_path):
    command = f"{ffprobe_exe} -v error -select_streams a -show_streams -of compact=p=0:nk=1 {video_path}"
    try:
        output = subprocess.check_output(command, shell=True).decode("utf-8")
    except subprocess.CalledProcessError as e:
        err_print(
            f"could not probe file, ffprobe failed with return code {e.returncode}"
        )
        exit(1)
    return len(output.split("\n")) - 1


quiet = args.quiet
dry = args.dry_run

if not dry:
    if not check_exe(ffmpeg_exe):
        err_print("ffmpeg not found")
        exit(1)
    if not check_exe(ffprobe_exe):
        err_print("ffprobe not found")
        exit(1)

in_path = args.input[0]
out_path = args.output[0]
total_duration = get_video_duration(in_path)
cvc = args.cvc
resolution = args.resolution[0]
framerate = args.framerate[0]
max_size = humanfriendly.parse_size(args.max_size[0], binary=True)
preset = args.preset[0]
start_time = parse_timestamp(args.start[0])
end_time = parse_timestamp(args.end[0])
tracks_num = get_audio_track_num(in_path)
tracks = parse_audio_tracks(args.audio_tracks[0])
if tracks is None:
    tracks = list(range(tracks_num))
audio_bitrate = humanfriendly.parse_size(args.audio_bitrate[0])
mix = not args.no_mix_audio
if end_time == -1:
    end_time = total_duration
trim_duration = math.ceil(end_time - start_time)

if not all(0 <= i < tracks_num for i in tracks):
    err_print(f"invalid audio track selection (file has {tracks_num} audio stream(s))")
    exit(1)
if cvc and (resolution != "keep" or framerate != 0):
    err_print("--cvc incompatible with specifying resolution and framerate")
    exit(1)
if resolution != "keep":
    res = parse("{w:d}x{h:d}", resolution)
    if res is None:
        err_print("invalid resolution")
        exit(1)
else:
    res = None
if trim_duration <= 0:
    err_print("invalid trim timestamps")
    exit(1)
if os.path.abspath(in_path) == os.path.abspath(out_path):
    err_print("input and output cannot be the same")
    exit(1)
if preset not in [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
]:
    err_print("invalid x264 preset")
    exit(1)

audio_size_bits = (
    (tracks_num if not mix else int(tracks_num > 0)) * audio_bitrate * trim_duration
)

video_bitrate = ((max_size - overhead) * 8 - audio_size_bits) // trim_duration

# TODO: provide an automatic way to determine the resolution based on the bitrate

base_command = " ".join(
    [
        f'"{ffmpeg_exe}"',
        f"-y",
        f"-v error",
        f"-ss {start_time}",
        f"-to {end_time}",
        f'-i "{in_path}"',
    ]
)

if cvc:
    base_command += " -c:v copy"
else:
    base_command += " ".join(
        [
            f"-c:v libx264",
            f"-preset {preset}",
            f"-b:v {video_bitrate}",
        ]
    )
    base_command += f" -r {framerate}" if framerate != 0 else ""
    base_command += f" -s {resolution}" if res is not None else ""

first_pass = " ".join(
    [
        base_command,
        f"-pass 1",
        f"-vsync cfr",
        f"-f null",
        "NUL" if os.name == "nt" else "/dev/null",
    ]
)

second_pass = " ".join(
    [
        base_command,
        # assume only one video stream
        f"-map 0:v:0",
    ]
)

if not cvc:
    second_pass += " -pass 2"

if tracks_num > 0:
    track_mapping = " ".join([f"-map 0:a:{i}" for i in tracks])
    second_pass += f" {track_mapping}"
    second_pass += f" -c:a aac -b:a {audio_bitrate}"
    if mix and tracks_num > 1:
        second_pass += f" -filter_complex amix=inputs={tracks_num}:duration=shortest"
else:
    second_pass += " -an"

second_pass += f' "{out_path}"'

if not cvc:
    info_print(
        f"* reencoding to {resolution}@{framerate}fps video with {video_bitrate // 1000}kbps"
    )
if mix and tracks_num > 1:
    info_print(f"* mixing audio tracks {tracks} with {audio_bitrate // 1000}kbps")
elif tracks_num > 0:
    info_print(f"* reencoding audio tracks {tracks} with {audio_bitrate // 1000}kbps")
else:
    info_print(f"* audio disabled")
info_print(f"* output path: '{out_path}'")
info_print(f"* output duration: {trim_duration}s")
if not cvc:
    info_print(
        f"* estimated output size: {humanfriendly.format_size(max_size, binary=True)}"
    )
if not cvc:
    info_print(f"* commands:\n\t{first_pass}\n\t{second_pass}")
else:
    info_print(f"* command:\n\t{second_pass}")

if dry:
    exit(0)

if not quiet:
    chose = False
    while not chose:
        print("Continue? [Y/n]:", end=" ")
        choice = input().lower()
        yes = {"y", "yes", ""}
        no = {"n", "no"}
        if choice in no:
            exit(0)
        elif choice in yes:
            chose = True

if cvc:
    try:
        subprocess.run(second_pass, shell=True).check_returncode()
    except subprocess.CalledProcessError as e:
        err_print(f"ffmpeg failed with return code {e.returncode}")
        exit(1)
    exit(0)

info_print("* running pass 1")
try:
    subprocess.run(first_pass, shell=True).check_returncode()
except subprocess.CalledProcessError as e:
    err_print(f"ffmpeg failed with return code {e.returncode}")
    exit(1)

info_print("* running pass 2")
try:
    subprocess.run(second_pass, shell=True).check_returncode()
except subprocess.CalledProcessError as e:
    err_print(f"ffmpeg failed with return code {e.returncode}")
    exit(1)

try:
    os.remove("ffmpeg2pass-0.log")
    os.remove("ffmpeg2pass-0.log.mbtree")
except:
    pass
