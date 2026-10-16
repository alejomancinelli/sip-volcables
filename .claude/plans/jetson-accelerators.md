# Plan: using the Jetson's fixed-function accelerators (template)

**Status: Phases 0 and 1 done and validated on the Jetson. Phase 3 implemented, waiting
for validation on the Jetson.** Engine telemetry reads sysfs without root. The work is on
`machinery/jetson-accelerators` in `sip-volcables`, branched from `fork/mock-pipelines` so
the Jetson clone can also run the mock pipelines on recorded video. It's machinery: these
commits go back to `cv_projects_template` as their own PR.

It's written so it can be picked up without the conversation that produced it: the
reasons are here, not by reference.

**Target module: Jetson Orin NX 8 GB** (JetPack 6.1, L4T R36.4, TensorRT 10.3), in the
20W power mode. It has VIC, NVENC, NVDEC, NVJPG and one DLA. Other modules are covered by
the `auto` fallback in §3, not optimized for.

---

## 0. What the Volcables benchmark already settled

An October 2026 benchmark campaign ran the Volcables workload (six cameras, three YOLO
models) on that Orin NX. Its reports are kept outside the repo. Only the conclusions that
shape this plan are copied here:

- **NVDEC plus a VIC resize at decode time was the largest input-side gain.** The frame
  came out of `nvv4l2decoder` already scaled by `nvvidconv` to the size the models use,
  with the bilinear filter (`interpolation-method=1`; the default nearest filter hurts
  small objects). That cut the model's CPU pre-processing sharply and raised FPS, with
  the same detections. CPU decoding with OpenCV cost much more CPU and was slower.
- **A rectangular engine matching the frame's aspect ratio was the largest model-side
  gain** (e.g. 384×640 for a 16:9 frame instead of 640×640, which is mostly padding).
  Agreed: export the engine to the ROI's or the frame's aspect ratio whenever the ROI is
  fixed or the aspect ratio changes.
- **`jetson_clocks` in the 20W mode** gave a clear gain, because Python pre- and
  post-processing run faster at pinned CPU clocks. It doesn't survive a reboot, so
  production needs a systemd unit. MAXN raised an over-current alert and is excluded.
- **DLA gave no gain**: none for the classifier, a large loss for segmentation. It's
  dropped from this plan (§5).
- **Output costs CPU, not detection FPS.** A mosaic on NVENC and per-camera MJPEG on NVJPG
  both worked. A local window on the Jetson's screen was the expensive output.
- **Post-processing in Python** (reading ultralytics results) is now a large CPU cost per
  inference. No engine helps there; it's for the YOLO wrapper to keep lean.

---

## 1. What we want, and the rule that decides everything else

Most installations run on a Jetson Orin. Besides the GPU, the SoC has fixed-function
engines that do one job cheaply and in parallel with the CPU and GPU:

| Engine | What it does | Where this project spends CPU on that job today |
|---|---|---|
| **VIC** | scale, crop, color convert, (approximate) remap | `cv2.resize` in the MJPEG server and the UI, `videoconvert`/`videoscale` in the RTSP pipelines, `cv2.remap` in `tools/image/undistort.py`, the letterbox of the model's input |
| **NVENC** | H.264/H.265 encode | already used by `video.rtsp.codec: h264_hw / h265_hw` |
| **NVDEC** | H.264/H.265 decode | `cv2.VideoCapture(CAP_FFMPEG)` in `rtsp_driver.py` and `video_file_driver.py`, on the CPU |
| **NVJPG** | JPEG encode/decode | `cv2.imencode(".jpg")` in `http_server.py`, once per frame per stream that has a client, and in `image_collector/collector.py` |

**The rule: the accelerated path is an implementation detail behind the same function,
never a second code path.** Each place in the table above keeps its signature
(`bytes = encode(frame_bgr)`, `frame_bgr = driver.get_frame()`, `frame = preprocessor(frame,
slot)`), and gets two backends: the current CPU one and the Jetson one. This is the same
approach as `h264_sw` / `h264_hw`, and as "a module whose system library is missing
degrades inside and exposes the same API". The difference is that here the module falls
back to the CPU backend instead of becoming a no-op.

## 2. Template or fork? Template.

The CLAUDE.md rule decides it: *if a file describes how something is done, it's machinery;
if it describes what is measured at this plant, it's the fork's.* Encoding a JPEG on NVJPG
instead of the CPU is *how*. No plant measures anything different because of it.

What **is** each fork's:

- **The TensorRT export of each model:**
  input size, precision, calibration set. These are files and config values, not code.
- **The config values**: whether a given installation forces the CPU backend, and the
  stream sizes and rates that the plant's encoder budget allows.
- **The module choice** (Orin Nano vs NX vs AGX), which decides which engines exist at
  all (§3).

If a fork has to edit machinery to get an accelerator, the extension point is missing.
Fix it in the template.

## 3. What happens on Windows, x86 Linux, and the different Jetsons

| Machine | Behaviour |
|---|---|
| **Windows dev PC** | CPU backends everywhere. The suite (no hardware, no GPU) only ever runs these, plus doubles of the Jetson backends. Nothing changes for development. |
| **x86 Linux, no NVIDIA GPU** | CPU backends. |
| **x86 Linux/Windows with an NVIDIA dGPU** | CPU backends at first. Desktop GPUs have NVENC/NVDEC too (GStreamer `nvcodec`: `nvh264enc`, `nvh264dec`), but no NVJPG or VIC, and the Jetson elements (`nvv4l2*`, `nvvidconv`, `nvjpegenc`) don't exist there. The backend registry (§4) leaves room for a `dgpu` backend later as one more entry. Don't build it until an installation actually runs on a dGPU. |
| **Jetson Orin Nano** | VIC and NVDEC. **No NVENC** (already documented in `rtsp_server.py`), **no DLA, no PVA**. The NVIDIA forum also reports **no JPEG encoder** (`NvJPEGEncoder` returns NULL). The probe in §4 must confirm this on the module. On a Nano, MJPEG and the dataset stay on the CPU, and RTSP stays on `_sw`. |
| **Jetson Orin NX** | VIC, NVENC, NVDEC and NVJPG. 1 DLA (8 GB) or 2 DLA (16 GB). |
| **Jetson AGX Orin** | All of the above, plus 2 NVJPG instances, 2 DLA and PVA v2. |
| **Jetson Thor** | Out of scope: it needs JetPack 7 (L4T R38), and `setup-jetson.sh` deliberately refuses anything but R36 (Python 3.10 is pinned by stapipy). |

The per-module cells come from secondary sources and forum answers, not datasheets. **The
truth for a given installation is what the probe reports on that module**, which is why
the probe is Phase 0 and not an afterthought.

### Selection: `auto` by default, visible, overridable

Each consumer gets a backend key with three values: `auto | cpu | jetson`.

- `auto` (default): use the Jetson backend if the probe found the engine, otherwise the CPU
  one. One `config.yaml` then works on the dev PC and the plant without a flag.
- `cpu`: force the CPU path. This is for comparing results and for working around a
  driver bug in the field.
- `jetson`: require it. If the engine is missing, this is a startup error with the reason,
  not a silent fallback. Use this at the plant once the budget in §6 assumes the engine.

The risk of `auto` is a plant Jetson quietly running on the CPU, so **the resolved backend
is always visible**: one log line at startup, and a column in the Diagnostics → Hardware
tab. RTSP keeps its explicit `codec` values, because they also pick the codec (H.264 vs
H.265), and `probe_codec()` already checks them.

The key lives in each consumer's own config section, because the hierarchy mirrors the
modules: `video.http.jpeg_backend`, `image_collector.jpeg_backend`,
`cameras.<slot>.decode_backend` (only `rtsp` and `video_file` drivers),
`cameras.<slot>.undistort.backend`.

## 4. Phases

Each phase can be merged and shipped on its own, and each has a measurement that decides
whether the next one is worth doing.

### Phase 0: measure and probe (do this first, it decides the rest)

**Done (steps 1 and 3), pending the run on the Jetson. Step 2 waits for that run.**

1. **`tools/jetson/`**: portable, knows nothing about the app, never raises, and returns
   empty values off a Jetson.
   - `board.py`: module, L4T release, power mode (`/var/lib/nvpmodel/status` +
     `/etc/nvpmodel.conf`), CPU/GPU clocks and whether they're pinned
     (`jetson_clocks` leaves min = max), and the over-current counters
     (`/sys/class/hwmon/hwmon*/oc*_event_cnt`). All of it readable without root.
   - `capabilities.py`: which GStreamer elements exist, plus **a short real pipeline per
     engine** (`videotestsrc` → engine → `fakesink`). An element can exist and still not
     use its engine. It also reports whether the venv's OpenCV has GStreamer/CUDA, the VPI
     version, and TensorRT's DLA count.
   - `tegrastats.py`: a parser for one `tegrastats` line, plus a reader that keeps one
     long-lived `tegrastats` process. It gives `{engine: {"mhz", "load_pct"}}` for GR3D,
     EMC, VIC, NVENC, NVDEC, NVJPG(1), OFA, DLA and PVA. Without root, sysfs only exposes
     the GPU's load; the other engines are only in debugfs.
2. **Engine load in `system_monitor.py`** and the telemetry. **Not done yet, on purpose:**
   - First the run has to confirm that `tegrastats` shows the engines **without root**.
     If it doesn't, the app can't read them and the source changes.
   - It also extends the `get_metrics()` contract, which `test_system_monitor.py` pins key
     by key. The plan for that:
     - a nested `engines` key (like `net_mbps`), flattened into
       `engine_<name>_mhz` / `engine_<name>_load_pct` fields in the `system` series;
     - `overcurrent_count`;
     - `docs/influxdb.md` and `docs/mqtt.md` updated in the same commit.
3. **`manual_test/jetson_accelerators/`**:
   - `probe_report.py` prints the board, the libraries, the elements, the raw `tegrastats`
     line as this user, and each engine's pipeline with whether that engine **lit up**
     while it ran.
   - `stage_bench.py` runs each stage CPU vs engine and reports wall ms/frame, **process
     CPU ms/frame** (all threads, GStreamer's included) and the engines seen. The stages:
     resize, MJPEG/dataset JPEG, the RTSP server's own `h264_sw`/`h264_hw` pipelines plus
     the Phase 1 variant, decode, debayer, undistort remap, and model letterbox (CPU, VIC +
     CPU, torch CUDA).
   - The point is the **round trip from a numpy frame**, which is what a GigE camera
     gives. The Volcables benchmark measured the VIC at decode time, with the frame
     already in NVMM, and that gain may not carry over.

   **Phases 1–4 only go ahead for stages that show up in this profile.**

#### First run (2026-10-15, Orin NX 8 GB, 20W, clocks not pinned)

Process CPU ms per 1920×1080 frame. Clocks weren't pinned, so these numbers are only
approximate.

| Stage | CPU path | Engine path | Reading |
|---|---|---|---|
| RTSP H.264 | `h264_sw` 43.8 · current `h264_hw` 23.9 | BGRx → nvvidconv → NVENC 6.9 † | **Phase 1 confirmed**, the largest win |
| Decode + resize to 640×360 | 22.1 | NVDEC + VIC resize 6.2 | **Phase 3 confirmed**, but only with the resize |
| Decode at full size | 13.5 | NVDEC 12.8 | No gain: the full-size BGRx→BGR copy costs what decoding saved |
| MJPEG 960×540 | 10.8 | NVJPG 6.8 † | Modest; Phase 2 stays conditional |
| Resize / letterbox from numpy | 6.0 / 10.3 | VIC 16.6 / 16.6 | **VIC loses**: the round trip costs more than the resize |
| Undistort remap | 109.6 | — | Very expensive |
| Debayer | 3.0 | — | Not worth moving |

† These two rows excluded the app's BGR→BGRx conversion. The bench now includes it, and
the second run re-measures them.

What changes in the plan:

- **No VIC for a frame that's already a numpy array** (GigE frames, display resize,
  letterbox). The VIC only pays when the frame is already in NVMM, i.e. coming out of
  NVDEC. Phase 4's VIC paths are dropped. Model input stays on the CPU, or moves to the
  GPU in the YOLO wrapper.
- **Phase 3 is NVDEC *with* resize at decode, or nothing.** Decoding at full size and
  copying out costs the same as the CPU path.
- **Undistort is the most expensive CPU stage measured.** It doesn't matter for a fork
  that doesn't enable it. For one that does, a VPI remap on CUDA comes first: VPI isn't
  in the venv today and has to be linked in, like `gi`.
- **Debayer stays on the CPU.**
- **`config.yaml` ships `video.rtsp.codec: h264_sw`.** On the NX that costs far more CPU
  than `h264_hw`, so a Jetson install should switch now, even before Phase 1.

**`tegrastats` publishes the engines only as root.** Without root, the line has
`GR3D_FREQ` and nothing else: no EMC, VIC, NVENC, NVDEC or NVJPG. With `sudo` it has all
of them. That rules out `tegrastats` as the app's source as it stands. The options, in
order of preference:

1. **sysfs without root**: runtime-PM state (`active`/`suspended`) and devfreq of each
   engine. That gives on/off and frequency, but no load %. The probe now prints both and
   samples them during each engine's pipeline. The second run says whether they exist.
2. **A sudoers rule pinned to the exact command**, e.g.
   `<user> ALL=(root) NOPASSWD: /usr/bin/tegrastats --interval 1000`, installed by
   `setup-jetson.sh`. It must never be a bare `tegrastats`: `--logfile` would let the
   user write any file as root.
3. GPU load only, which sysfs already gives the app today.

The first run's "NO SE ENCENDIÓ" (didn't turn on) on every pipeline was a bug in the
probe: an engine that `tegrastats` doesn't report was being read as off. The probe now
says "no publicado" (not reported). The NVENC/NVDEC lines in its stderr show the engines
did run.

#### Second run (2026-10-15, same board, clocks pinned with `jetson_clocks`)

The bench now charges the engine variants for the BGR→BGRx conversion.

| Stage | CPU path | Engine path | Reading |
|---|---|---|---|
| RTSP H.264 | `h264_sw` 37.3 · current `h264_hw` 22.7 | Phase 1 12.3 | **Phase 1 confirmed**: about half of today's `h264_hw` |
| Decode + resize to 640×360 | 20.2 | NVDEC + VIC 5.6 | **Phase 3 confirmed** |
| Decode at full size | 11.7 | NVDEC 9.9 | Small gain |
| MJPEG 960×540 | 10.8 | NVJPG 11.4 | **No gain** once the conversion is charged |
| Resize / letterbox from numpy | 5.8 / 9.8 | VIC 12.1 / 17.0 | VIC loses, as in the first run |
| Undistort remap | 106.4 | — | |

**The rule both runs show: an engine pays when the frame is already in its memory.**
- A frame out of NVDEC is in NVMM, so the VIC resize there is nearly free. That's what
  Volcables measured with RTSP cameras, and what `nvdec + vic resize` shows here.
- A frame that's a numpy array (a GigE camera's, or anything the app has already touched)
  has to be copied up and back. Those copies cost more than the VIC or NVJPG save.
- That's why NVJPG for MJPEG and the dataset is dropped (Phase 2), along with the VIC for
  display resize and letterbox. NVJPG would come back only fed from NVDEC directly, which
  the MJPEG server doesn't do: it encodes the annotated frame, which is a numpy array.

**Engine telemetry has a source without root: sysfs.** Run as a normal user, each engine's
pipeline turned its runtime-PM node `active` and raised its devfreq clock. Root
`tegrastats` confirmed every one of them really ran (e.g. `nvenc 81%`, `nvdec 79%`,
`vic 38%`). So step 2 reads sysfs, with no sudoers rule. What it can and can't say:
- **`active` means "used in the last few seconds"**, not "busy now". The kernel delays
  autosuspend, so VIC still showed `active` in the variant after the one that used it.
  At one reading per telemetry tick, that's the right granularity.
- **Frequency only means something while `active`.** NVJPG and OFA report their maximum
  clock while suspended. Publish the MHz only when the engine is active, and 0 otherwise.
- **No load %.** That stays root-only. A future option, if it's ever needed: a sudoers
  rule pinned to the exact `tegrastats` command.
- The node names (`15340000.vic`, `154c0000.nvenc`, ...) are the Orin's register
  addresses. Telemetry should publish them under the engine name (`vic`, `nvenc`,
  `nvjpg`, `nvjpg1`...), so a dashboard doesn't change with the module.

### Phase 1: fix the RTSP path that already uses NVENC (small, low risk)

**Done and validated on the Orin NX.**
- **Bench:** `stage_bench.py h264` with clocks pinned, 1080p, process CPU ms per frame:
  `h264_sw` 37.6, previous `h264_hw` 22.4, new `h264_hw` **12.0**. It's also about 3×
  faster in wall time (6.5 ms vs 19.1).
- **End to end:** `manual_test/rtsp_video/` with VLC. Correct colours, scaling on the VIC,
  bitrate, and a picture within a second when joining mid-stream.
- **Bug found on the way, older than this branch:** the server was attached to GLib's
  global context instead of its own thread's. Without a Qt loop dispatching that context,
  clients hung at OPTIONS. Fixed.

What went in:
- The `_hw` codecs take BGRx: the pusher converts with OpenCV, and `nvvidconv` scales
  (bilinear) and converts to NV12 in NVMM.
- Mono frames go as `GRAY8` on both paths.
- Every H.264/H.265 codec gets a keyframe per second. x264's default was ~250 frames,
  more than 15 s at 15 fps before a joining client saw an image.
- New key `video.rtsp.bitrate_kbps`, in the config tab too, converted to each encoder's
  unit.
- Not added: `maxperf-enable`, `preset-level` and `insert-sps-pps`. `rtph264pay
  config-interval=1` already sends SPS/PPS, and the other two weren't measured.

A change from the Config view takes effect when a stream is reopened, with no restart.
That's how two codecs or bitrates get compared on an installed system.

The design that came before the implementation:

The `_hw` pipelines in `rtsp_server.py` still do two CPU steps before the hardware:
`videoscale` (CPU scaling) and `videoconvert ! format=I420/NV12` (a full CPU color
conversion). `nvvidconv` can't take packed 3-channel BGR, but it does take `BGRx` and
`GRAY8`. The changes:

- `videoconvert ! video/x-raw,format=BGRx`: just adds a padding byte, much cheaper than
  BGR→I420. Mono cameras push `GRAY8` directly.
- Move the scaling into `nvvidconv`, so the VIC scales and converts in one pass. The
  `capsfilter` that `_declare_format()` fixes then goes *after* `nvvidconv`, in NVMM
  memory. The `_sw` pipelines keep `videoscale`.
- Set the encoder properties explicitly. The defaults aren't what a live stream wants:
  - `bitrate` defaults to 4 Mbps, which smears a 5 MP frame, so expose it in
    `video.rtsp.bitrate_kbps`;
  - `iframeinterval`/`idrinterval` near the fps, so a client that joins mid-stream gets an
    image fast;
  - `insert-sps-pps=true`;
  - `maxperf-enable=true`;
  - `preset-level`.
- `appsrc` currently gets `frame_bgr.tobytes()`, which is one more full copy. Look at
  `Gst.Buffer.new_wrapped_full` over the numpy buffer, while keeping the array alive for
  the buffer's lifetime.

### Phase 2: NVJPG for MJPEG and the dataset (dropped)

**Dropped after the second run.** From a numpy frame, NVJPG costs as much CPU as
`cv2.imencode`, once the BGR→BGRx conversion and the copies are counted. The design below
stays for reference, in case a path ever feeds it a frame that's already in NVMM.

- **`tools/image/jpeg.py`**: `encode_jpeg(frame, quality) -> bytes | None`, with a CPU
  backend (`cv2.imencode`) and a Jetson backend. The Jetson backend is a long-lived
  GStreamer pipeline per (size, format): `appsrc ! nvvidconv ! nvjpegenc ! appsink`. It
  can also scale on the VIC first, so `http_server._downscale()` goes into the same pass.
  The pipeline is rebuilt when the frame size changes, like `_declare_format()` does
  today.
- `http_server.py` and `collector.py` call it instead of `cv2.imencode`.
- **The dataset is the delicate consumer.** It's "the only thing that can't be
  regenerated", and models are retrained on it. NVJPG and libjpeg don't produce identical
  files at the same quality number: quantization tables and chroma subsampling can differ.
  Before switching the dataset backend, compare a few hundred frames: PSNR, and the
  offline runner's results on CPU-encoded vs NVJPG-encoded copies. If the dataset is saved
  as PNG, it stays on the CPU: there is no PNG engine.

### Phase 3: NVDEC for RTSP cameras and recorded video

**Implemented; pending validation on the Jetson.** How it landed:
- **`tools/camera/decoder.py`** is the one place that knows there are two ways to decode.
  - `open_stream()` / `open_file()` return a `Capture` with the same API on both
    backends: `grab`, `retrieve`, `read`, `seek`, `get_fps`, `release`.
  - `rtsp_driver` and `video_file_driver` only call that. The RTSP driver keeps its
    frame credit: `grab()` pulls the sample, and the BGRx→BGR copy is only paid for
    frames that get delivered.
- **Config per camera:** `decode.backend` (`cpu` default | `nvdec` | `auto`) plus
  `decode.width_px` / `height_px`.
  - **The size is applied by both backends**: the VIC on `nvdec`, `cv2.resize` on `cpu`,
    both bilinear. Switching backend never changes the frame the app sees.
  - `nvdec` where it doesn't exist is an error at config read, and the camera doesn't
    open. No silent fallback to the CPU.
- **NVDEC pipelines:**
  - RTSP: `rtspsrc protocols=tcp ! parsebin ! nvv4l2decoder disable-dpb=true !
    nvvidconv ! BGRx[,size] ! appsink drop=true max-buffers=1`. The credentials go as
    `user-id`/`user-pw` properties, never inside the launch string.
  - Files: no frame dropping and no `disable-dpb`, because recordings may have B-frames.
- **The bench's `decode` stage now runs `decoder.open_file()`**: it measures the line's
  code, not a copy of it.
- **`manual_test/cameras/config.yaml`** has `rtsp_local_nvdec` / `rtsp_local_cpu`, which
  read the `rtsp_video` test server as a fake IP camera, and `video_nvdec`.
- **Not done:** decode settings in the cameras tab of the Config view. The keys are saved
  one by one, so a save from the UI keeps the `decode` section.

The design that came before the implementation:

- **OpenCV can't do this here.** The venv installs `opencv-python-headless` from PyPI,
  which has no GStreamer backend, so `cv2.VideoCapture("... ! nvv4l2decoder ...",
  CAP_GSTREAMER)` doesn't exist. Rebuilding OpenCV for the Jetson isn't worth it either:
  it's a new per-JetPack build artifact. The decode backend uses the same PyGObject `gi`
  that `rtsp_server.py` already uses:
  - RTSP: `rtspsrc latency=0 ! rtph264depay ! h264parse ! nvv4l2decoder ! nvvidconv ! video/x-raw,format=BGRx ! appsink drop=true max-buffers=1 sync=false`
  - file: `filesrc ! qtdemux ! h264parse ! nvv4l2decoder ! ...`, the same tail.
- `appsink drop=true max-buffers=1` keeps the driver's **always-fresh** contract.
  The `grab()`/`retrieve()` trick in `rtsp_driver.py` (skip decoding frames over the
  credit) stops mattering: NVDEC decodes everything and that's free for the CPU. The
  credit logic stays, applied after the appsink.
- `video_file_driver.py` has to keep pacing by the clock (a slow consumer loses frames,
  the video doesn't stretch). The loop and per-clip fps logic stay in the driver. Only
  "give me frame N" changes backend.
- BGRx → BGR is a numpy slice plus a copy. Measure whether that copy eats the gain at the
  camera's resolution.
- Credentials (`credentials_env`) and the error messages ("host unreachable, wrong path or
  rejected credentials") go into the new backend unchanged.

#### What the decode size changes, and the ROI crop on the VIC (not built)

With `decode.width_px/height_px` set, the camera *is* that size for the whole app:
inference, the ROI, the dataset, the annotated frame, both `raw` and `annotated` streams,
and the UI. The camera still sends full resolution, and NVDEC decodes it at full size; the
VIC discards the rest. Four consequences to keep in mind when choosing a size:

1. **The ROI is cropped after the resize, so a small ROI loses detail.** Example: a ROI a
   third of the frame wide is 640 px at 1080p, but 213 px at 640×360, and the model then
   gets it stretched about 3×. Cropping before the resize keeps every pixel of the ROI.
   **Rule: the ROI's width at the decoded size should be at least the model's input
   width.** If it isn't, decode larger (1280×720) or at native size. ROI coordinates are
   in pixels of the decoded frame and aren't rescaled: changing the decode size means
   redrawing the ROI and recalibrating lens health.
2. **The model's input size sets the floor, not the size of its training photos.** A
   model trained at `imgsz=640` on 1080p images already saw them shrunk to 640 wide, so
   640×360 frames give it practically the same pixels. One trained at `imgsz=1280` needs
   at least 1280×720.
3. **The dataset is collected at the decoded size**, and it's the one thing that can't be
   regenerated. If a larger model might be trained later, collect at native size and
   switch to the small size for production.
4. **Width and height are both fixed, so the aspect ratio isn't preserved.** A
   replacement camera with a different aspect (4:3 instead of 16:9) comes out squashed
   until `decode` is updated. The settings belong to the slot, not to the device. A later
   improvement could derive the height from the source's caps.

**Full-resolution viewing or recording** doesn't need the Jetson. The camera is an RTSP
server with several clients, so an NVR can read it directly. That only fails if the NVR
can't reach the camera network (cameras behind a second NIC on the Jetson); then the
camera has to be decoded at native size, paying the full-size copy.

**The ROI crop on the VIC (idea, not built).** `nvvidconv` can crop as well as scale
(`left`/`right`/`top`/`bottom`). NVDEC → crop the ROI → resize would give the model the ROI
with full detail at almost no CPU cost. What it would take:
- **The app would only ever see the crop**: the streams, the UI and the dataset. Then
  there's no full frame left to draw the crop on, so the drawing needs its own mode
  that decodes the full frame once.
- **The crop is in source pixels** and belongs in `decode` (e.g. `decode.crop`), separate
  from the engine's ROI, which would then be relative to the crop or turned off.
- **A camera that several pipelines read** (the case in sip-volcables) shares one decode,
  so one crop has to cover every pipeline's region. A second output per pipeline (a
  `tee` with two VIC branches) costs a second copy into numpy.
- Volcables' benchmark recommended exactly this for the grain classifier (a fixed region
  instead of the centre crop). It's the next step for Phase 3 if a small ROI ever has to
  be read with detail.

### Phase 4: VIC / CUDA for image preprocessing

The candidates are the ones the Phase 0 profile flags. The likely ones:

- **The model's input preparation** (letterbox resize + normalize + HWC→CHW). Ultralytics
  does it on the CPU with cv2 and numpy, on a frame that can be 5 MP. This belongs in the
  YOLO wrapper that `sip-volcables.md` §"Models" already plans to send back to the
  template. Do it on the GPU: upload once, then resize/normalize with torch, since the
  wrapper already has torch. That's simpler and more exact than a VIC round trip. **This
  is probably the single biggest CPU win**, and it doesn't touch any of the four engines.
- **Undistort** (`tools/image/undistort.py`). VPI's `Remap` runs on CUDA or VIC. The VIC
  approximates the dense map with a sparse warp grid, so the corrected frame isn't
  pixel-identical to `cv2.remap`. Undistort decides *where a pixel is*, and that feeds
  every position-based measurement. Default to the **CUDA** backend, and allow VIC only
  after the offline runner shows the metrics don't move.
- **Debayer** (`st_driver.py`, and pylon's converter in `basler_driver.py`). The VIC
  doesn't debayer (the ISP does, but only for CSI cameras). There are two options, both to
  measure: a CUDA debayer (NPP or a small kernel), or let the camera output BGR8. The
  second costs 3× GigE bandwidth, so it only works with few cameras per NIC.
- **Display-only scaling** (UI `camera_panel`, the HTTP downscale if Phase 2 didn't
  absorb it). VIC through VPI or `nvvidconv`.

**VPI** is NVIDIA's Python API over CUDA/VIC/PVA/OFA (`python3-vpi3` from apt, with Python
3.10 bindings on JetPack 6). It gets linked into the venv the same way `venv-setup.sh`
already links `gi`, and the Nuitka build has to carry its `.so` files.

### Phase 5: DLA (dropped)

The Volcables benchmark found no gain for the classifier and a large loss for
segmentation (§0). Nothing is built. The `inference.models.<slot>.dla_core` key stays an
idea for a model that is fully DLA-compatible, if one ever appears.

## 5. Other accelerators worth using

| Accelerator | Worth it? | Why |
|---|---|---|
| **DLA** (Deep Learning Accelerator) | **No; tested.** | On paper it fits: one inference thread per pipeline, so a model could move to DLA0 with no scheduling code. In practice, on the Orin NX 8 GB, it gave no gain for the classifier and a large loss for segmentation. Dropped (§0). |
| **GPU (CUDA) for pre/post-processing** | **Yes.** | Not a fixed-function engine, but the largest remaining CPU cost after the encoders is usually letterbox/normalize, plus NMS and mask decode in Python. Phase 4. |
| **OFA** (Optical Flow Accelerator, via VPI) | **Maybe, fork-specific.** | Dense optical flow nearly for free. For sip-volcables, "is grain falling" is a shapeless stream with nothing to box, and flow magnitude in the ROI is a strong signal for that. It doesn't replace the classifier, but it could gate it or confirm it. Research item, not machinery. |
| **PVA** (Programmable Vision Accelerator) | Low. | Only reachable through VPI, which has a short list of algorithms (KLT tracking, filters, stereo). Nothing in the current pipeline maps onto it. Not on the Nano. |
| **ISP** | Not applicable. | Only for CSI/GMSL sensors through Argus. GigE cameras debayer in the camera or on the host. |
| **Power mode / clocks** | Yes, as setup, not code. | `nvpmodel` and `jetson_clocks` set the clocks of the GPU, VIC, NVENC and EMC too. A Jetson in a low power mode makes every engine look slow. `setup-jetson.sh` should set and report the mode, and the benchmark should record it. |

**Considered and rejected: DeepStream / `jetson-utils`.** DeepStream would move the whole
pipeline into GStreamer: capture, inference, overlay, everything. That replaces the
template's architecture instead of accelerating it, and it doesn't take GigE SDKs as
sources without custom plugins. `jetson-utils` is built from source per JetPack, which is
the kind of build drift `constraints.txt` exists to prevent. GStreamer + VPI + TensorRT
are already in JetPack and cover the four engines.

## 6. What has to be taken into account

### Model export to `.engine`

- **An engine is tied to the GPU architecture, the TensorRT version and (in practice) the
  module.** Export **on the Jetson model and JetPack the plant runs**, never on the dev PC.
  An Orin Nano and an Orin NX share SM 8.7, but TensorRT tunes tactics per device. Keep the
  ultralytics version pinned in `constraints.txt` (it writes its own metadata header into
  the engine).
- **The input size is fixed at export.** Pick it from the ROI, not the full frame:
  - The engine already sees the ROI crop (`engine.py` crops before `predict()`). Export a
    **rectangular** `imgsz` with the ROI's aspect ratio (e.g. 640×384 for a wide ROI), so
    the letterbox doesn't waste a third of the input on padding.
  - Size it from the **smallest object you need**: after scaling, it should still be
    roughly ≥ 10–16 px at the model input. That sets the floor, and GPU time sets the
    ceiling. Each axis must be a multiple of the model's stride (32 for YOLO).
  - If a fork changes the ROI's shape, the engine must be re-exported. Write the export
    `imgsz` into the encrypted weights' metadata. The wrapper can then warn when the ROI's
    aspect ratio is far from it.
- **Precision.** Use FP16, exported on the Jetson. INT8 is the next reserve when more
  headroom is needed, and Volcables measured it as faster still. It needs a calibration
  set: use the image collector's dataset from **that** plant, and re-check accuracy with
  the offline runner, because INT8 can lose small objects.
- **Cap the builder's workspace** (`workspace=1` in ultralytics' export). On the 8 GB board
  an uncapped engine build once ran out of memory with the desktop open.
- **No DLA** (§0, §5).
- **No batching across cameras.** Volcables tested it: a little more throughput, but every
  camera waits for the whole batch and latency got much worse. One frame at a time, as the
  engine works today.

### Memory and copies: the trap that cancels the gain

- On a Jetson the CPU, GPU and every engine share the same DRAM. An accelerator that needs
  the frame in a different layout (NVMM, block-linear, NV12) pays a conversion copy to get
  there and another to get back. **Offloading a 2 ms job and adding two 3 ms copies is a
  loss.** That's why Phase 0 measures end to end, and why each backend must keep the frame
  in the engine's memory for as many consecutive steps as possible (scale + convert +
  encode in one GStreamer pipeline, not three round trips).
- Frames come out of the GigE SDKs in system memory and will stay there: there is no
  zero-copy path from stapipy/pypylon into NVMM. The gain is in moving **compute** off the
  CPU, not in avoiding the first copy.
- Watch EMC (memory bandwidth) load, not only CPU and GPU. Five cameras at 5 MP BGR at 15
  fps is ~1.1 GB/s *before* anything is copied.

### Formats and sizes

- `nvvidconv`/VIC take `BGRx`, `RGBA`, `NV12`, `I420`, `GRAY8`. They don't take packed
  `BGR`.
- Encoders and JPEG with 4:2:0 chroma want **even width and height** (already handled in
  `_compute_scaled_size`). Some engines also want pitch alignment: let `nvvidconv` handle
  it, don't write raw buffers to it by hand.
- Each engine has a resolution ceiling and a session budget. NVENC on an Orin NX is about
  one 4K60 or a handful of 1080p streams total. With N cameras × {raw, annotated} × {RTSP,
  MJPEG}, **count the concurrent streams against that budget**. The streams are gated by
  client, so the worst case is everything watched at once.

### Results must not change silently

- A VIC or CUDA resize doesn't use the same interpolation as `cv2.resize`, so the model
  receives slightly different pixels from the ones it was trained on. This is the same
  principle as `image_adjust` ("exists to reproduce, not to tune"). **Any accelerated
  step that feeds the model or a measurement is validated with the offline runner**: run
  the same image set with `cpu` and `jetson` backends and compare metrics. Steps that only
  feed display (MJPEG, RTSP, UI) don't need this.
- The offline runner and future ground truth run the line's code. On the Jetson they must
  use the same backends as the line. On the dev PC they use the CPU ones, so any ground
  truth tolerance has to absorb the difference, or ground truth is produced on the Jetson.

### Tests, build and setup

- The suite stays hardware-free: each Jetson backend is tested against a double of `gi` /
  `vpi` / TensorRT inside its test file, the way `gpio_control` is tested against a double
  of `gpiod`. That covers selection (`auto/cpu/jetson`), fallback, the probe's "never
  raises", and the backend reported to diagnostics. Real engines are tested in
  `manual_test/jetson_accelerators/`.
- `setup-jetson.sh`: add the packages (`nvidia-l4t-gstreamer` is normally present;
  `python3-vpi3` and `vpi3` if Phase 4 lands), and a final check that runs the probe and
  prints the module's capability table.
- `build/` (Nuitka): make sure the build carries the `vpi` binding and that the GStreamer
  plugin path still resolves from the compiled launcher.
- Docs: a `docs/jetson.md` for the integrator (which module gives which engines, the
  stream budget, how to read the new telemetry fields). Update `CLAUDE.md` (map, decisions)
  and `docs/influxdb.md`/`docs/mqtt.md` for the new fields.

## 7. Suggested order and expected value

For the Orin NX 8 GB, with the Volcables results and Phase 0's first run in hand:

1. ~~Finish Phase 0: step 2.~~ Done: `engines` and `overcurrent_count` in
   `get_metrics()`, `engine_<engine>_active` / `_mhz` in the `system` series.
2. ~~Phase 1.~~ Implemented; validate on the Jetson with `manual_test/rtsp_video/` and
   `stage_bench.py h264`.
3. ~~Phase 3 (NVDEC + VIC resize at decode)~~. Implemented; validate on the Jetson. This
   was proven twice, and Volcables' cameras are RTSP.
   - The decoder's output size makes it a 640×360 camera as far as the app knows: the
     ROI, the dataset and the streams all live at that size. So it's a per-camera config
     key, like a camera's resolution, not a preprocessing step.
   - Without the resize, it isn't worth doing.
4. ~~Rectangular engines~~ (§6). Implemented; validate on the Jetson with a real engine.
   - **Runner:** `system/inference/models/yolo_model.py` (`yolo_det`, `yolo_seg`,
     `yolo_cls`) on `tensorrt_runtime.py`, which is our own TensorRT runner and not
     ultralytics.
     - It loads the `.engine` from bytes, so encrypted weights work, and reads
       ultralytics' metadata header for the task check and class names.
     - It takes the input size from the engine itself, so the square-vs-rectangular
       switch is just which file `path` points to.
     - Pre and post in numpy/OpenCV: ultralytics' exact letterbox, class-aware NMS,
       masks from the prototypes, and the classifier's centre crop. The GPU is only
       used for the TensorRT pass. Buffers are torch CUDA tensors, so ultralytics isn't
       needed at runtime.
   - **Export:** `manual_test/model_export/export_engine.py` with `rectangular:
     true/false` per model. It computes the input from the source's aspect ratio (frame
     or ROI), rounded up to a multiple of 32, and names the file with its size
     (`det_384x640_fp16.engine`). Classifiers are always exported square.
   - **Not done:** letterbox on the GPU. With NVDEC decoding at 640×360 it's almost only
     padding; measure on the Jetson before moving it.
5. ~~Phase 2 (NVJPG)~~. Dropped: no gain from a numpy frame (second run).
6. **Undistort on VPI/CUDA**, only for a fork that enables undistort. The rest of
   Phase 4's VIC paths are dropped.

## 8. Open decisions

- **Whether `auto` is acceptable at the plant**, or whether deployed configs must say
  `jetson` explicitly so a missing engine stops the startup instead of loading the CPU.
- **Whether a dGPU backend is ever needed** (x86 industrial PC with an NVIDIA card). The
  registry leaves room for it; nothing is built until one exists.
- ~~Platform setup as code.~~ Done in `setup-jetson.sh`, step 7/8. Each part has its
  own flag: `--power-mode=NAME`, `--clocks-at-boot` (a systemd oneshot that runs
  `jetson_clocks` after `nvpmodel.service`) and `--headless-boot`. Without the flags the
  step only reports the board's settings, because the power mode is a decision about
  the plant's enclosure.
