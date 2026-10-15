# Plan: using the Jetson's fixed-function accelerators (template)

**Status: Phase 0 implemented, waiting for its first run on the Jetson.** The work is on
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

### Phase 1: fix the RTSP path that already uses NVENC (small, low risk)

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

### Phase 2: NVJPG for MJPEG and the dataset

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

For the Orin NX 8 GB, with the Volcables results in hand:

1. **Phase 0.** Run both scripts on the Jetson, then do step 2 with whatever `tegrastats`
   turns out to show without root.
2. **Phase 3 (NVDEC + VIC resize at decode)** for RTSP cameras and `video_file`. This is
   the one already proven, and Volcables' cameras are RTSP. Its decoder output size
   becomes a per-camera config key, so the frame reaches the engine at the size the model
   uses.
3. **Phase 1.** Hours of work on code that already exists. It removes two CPU conversions
   from every RTSP stream.
4. **Rectangular engines** (§6) in the YOLO wrapper and its export notes. This is a
   fork/export decision, not engine code, but it was the largest model-side gain.
5. **Phase 2 (NVJPG)**, if the bench shows the round trip from numpy still pays off.
6. **Phase 4.** Model input on the GPU or the VIC, then undistort/debayer, as the
   profile dictates.

## 8. Open decisions

- **Whether `auto` is acceptable at the plant**, or whether deployed configs must say
  `jetson` explicitly so a missing engine stops the startup instead of loading the CPU.
- **Whether a dGPU backend is ever needed** (x86 industrial PC with an NVIDIA card). The
  registry leaves room for it; nothing is built until one exists.
- **Platform setup as code.** The power mode, a systemd unit for `jetson_clocks`, and a
  headless boot target (`multi-user.target`). They could live in `setup/jetson/`, or stay
  per-plant commissioning steps.
