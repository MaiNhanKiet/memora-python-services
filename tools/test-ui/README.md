# MEMORA Test UI (chỉ dùng local dev)

Một trang web mô phỏng luồng vân tay / soundwave **không cần backend**: trang đóng vai Gateway + Operations (đơn hàng giả, tạo phiên, nhận ảnh từ ESP32 qua MQTT, gọi worker Python, nhân viên chọn / từ chối). Tab 3 vẫn giữ các mục A-H gọi backend thật.

**Không deploy.** Chỉ chạy trên máy dev, không có xác thực riêng.

## Yêu cầu

- Tab 3 (mục A-F): API Gateway chạy tại `http://localhost:3000` (prefix `/api/v1`), cùng các service iam / commerce / operations. Tab 1 và 2 không cần.
- Worker Python chạy tại `http://127.0.0.1:8010` (tab 1, 2 và mục G).
- File `services/personalization_worker/.env` có `WORKER_SHARED_SECRET` và `S3_*` (server đọc lúc chạy, không in ra, không gửi cho trình duyệt).
- Chrome hoặc Edge cho Web Serial (mục H) và ghi âm (tab 2, mục F). MQTT cần tải `mqtt.js` từ unpkg.com (cần Internet).

## Chạy

```bash
services/personalization_worker/.venv/Scripts/python tools/test-ui/server.py
```

Mở `http://127.0.0.1:8090`. Chỉ dùng thư viện chuẩn + `boto3`, `python-dotenv` đã có trong venv của worker.

Cấu hình tùy chọn qua biến môi trường hoặc `tools/test-ui/.env` (đã gitignore, xem `.env.example`): `GATEWAY_URL`, `WORKER_URL`, `WORKER_ENV_PATH`, `TEST_UI_PORT`, `MODELS_DIR` (thư mục model 3D, mặc định `<repo>/../memora-3d`).

## Luồng mô phỏng (tab 1 và 2)

Điều kiện:

- Worker Python đang chạy (pill "worker" ở thanh trên cùng phải ok: potrace, ffmpeg, storage).
- **Tắt operations-service** (server và local) trong lúc test, vì nó cũng sẽ trả lời thiết bị và gây xung đột `results`.
- ESP32 ở chế độ MQTT, đã vào Wi-Fi, dùng cùng broker.
- Thanh trên cùng: nhập mật khẩu MQTT (không lưu, không ghi log), bấm Kết nối MQTT. Ô Thiết bị mặc định `esp32-store-01`, tự đổi theo `hello` đầu tiên nếu bạn chưa sửa tay.

Đơn hàng giả (orderId, "Khách test", "Đã thanh toán P1", consent mô phỏng, Item 1 FINGERPRINT `SIGNET_001_FP`, Item 2 SOUNDWAVE `BAR_001_SW`) được tạo lần đầu và giữ trong sessionStorage; nút "Tạo đơn mới" tạo lại.

**Tab 1 Vân tay:**

1. Bấm "Tạo phiên": trang gửi `start` (QoS 1) tới `memora/capture/{device}/commands` (tối đa 3 lần, hết hạn sau 15 phút). Khi thiết bị gửi lại `hello` lúc phiên còn mở, trang gửi lại cùng `start`.
2. Bấm nút trên ESP32 để quét. Mỗi `attempts` được trả `results` PENDING_REVIEW (gợi ý PASS nếu quality >= ngưỡng, mặc định 50), nonce lặp thì gửi lại kết quả cũ, không ảnh thì REJECTED `CAPTURE_FAILED`. Sau đó BMP được upload và worker xử lý tự động.
3. Mỗi lần quét là một thẻ: BMP gốc, PNG alpha kèm hull, SVG, điểm, trạng thái worker, nhãn PENDING_REVIEW / REJECTED / ACCEPTED / NOT_SELECTED.
4. "Từ chối" gửi `results` REJECTED `STAFF_REJECTED` (hết lượt và không còn lần chờ thì phiên FAILED và gửi `end` FAILED). "Chọn lần này" gửi `end` COMPLETED kèm `acceptedAttemptNo`. "Lần tiếp theo" gửi `next` (chỉ bật khi thiết bị đang chờ nhân viên). "Kết thúc không chọn" gửi `end` CANCELLED.
5. Hết hạn: phiên chuyển EXPIRED ngay trên giao diện (không gửi lệnh nào). Dòng thời gian ghi mọi frame MQTT (base64 được rút gọn), lời gọi worker và thao tác nhân viên.

**Tab 2 Soundwave:** ghi âm (tối đa 30 s) hoặc chọn file WAV/MP3/M4A/AAC. Định dạng khác (kể cả bản ghi) được đổi sang WAV mono 16-bit. Kéo cửa sổ 3 s trên sóng (hoặc nhập `segmentStartMs`), nghe thử 3 s, rồi bấm "Xử lý" (thời lượng phải 3000..30000 ms). Kết quả: measuredDurationMs, 23 bar (tham chiếu vàng, kiểm tra xanh), levels, checkBars, PNG/SVG, `audio_segment.wav`, danh sách nudge với nút "Render mã này". Lỗi worker hiển thị dạng `code: message`.

Cấu trúc object trong Garage (gói, đơn hàng, phiên), mô phỏng đúng luồng thật:

```text
personalization/test-ui/
  fingerprint/{orderId}/
    sessions/{sessionId}/
      attempt-{n}.bmp          raw từng lần quét (xóa khi REJECTED / NOT_SELECTED / EXPIRED / CANCELLED)
      preview-{n}/             worker chạy để nhân viên xem trước (xóa cùng raw; lần được chọn thì xóa preview)
    selected/{sourceId}/       worker chạy lại cho lần được chọn ("Chọn lần này")
  soundwave/{orderId}/
    uploads/{sourceId}/raw.{wav|mp3|m4a|aac}
    selected/{sourceId}/       kết quả worker; "Render mã này" ghi đè vào cùng thư mục
```

Mỗi request worker mang `context`: vân tay `{orderId, sessionId, attemptNo, ringItemIds:[item1Id]}` (bản preview không có `ringItemIds`), soundwave `{orderId, ringItemIds:[item2Id]}`. Đường dẫn storage hiển thị (chữ nhỏ) trên thẻ từng lần quét và khung kết quả soundwave để tìm trong Garage Web UI. Nút "Dọn dữ liệu test của đơn này" xóa `fingerprint/{orderId}/` và `soundwave/{orderId}/`; có thể xóa tay toàn bộ `personalization/test-ui/` sau khi thử.

## Tab 3: các mục A-H (qua backend)

- **A. Tài khoản**: hai ô (khách hàng / nhân viên), đăng nhập `POST /auth/login`. Access token lưu trong sessionStorage. Refresh cookie do proxy giữ trong bộ nhớ server theo từng ô (hai tài khoản không đè nhau). Nút Refresh gọi `GET /auth/csrf` rồi `POST /auth/refresh` với `X-CSRF-Token`.
- **B. Đơn hàng**: liệt kê đơn của khách, chọn đơn, xem ring item (template, gói, version). Có ô nhập order id thủ công.
- **C. Consent**: đồng ý (FINGERPRINT_PROCESSING / SOUNDWAVE_PROCESSING, policyVersion mặc định `dev-1`), danh sách, thu hồi.
- **D. Phiên lấy vân tay (nhân viên)**: tạo phiên, tự cập nhật mỗi 2 s, bảng các lần quét có ảnh, nút Lần tiếp theo, Từ chối lần, Chọn lần này, Kết thúc không chọn.
- **E. Nguồn và xử lý**: danh sách nguồn (PERSO-011) và trạng thái xử lý (PERSO-012), tự poll tới READY / PROCESSING_FAILED.
- **F. Soundwave**: chọn file hoặc ghi âm (tối đa 30 s), đo thời lượng, sha256, chọn đoạn 3 s, initiate, PUT thẳng lên Garage theo `uploadUrl` / `uploadHeaders`, complete, rồi theo dõi xử lý.
- **G. Worker**: `/worker/health`, upload ảnh (BMP/PNG) hoặc audio qua `/dev/upload-raw`, gọi `/v1/fingerprint/process` hoặc `/v1/soundwave/process`, xem JSON, PNG alpha kèm hull, SVG, WAVECODE (levels, bars) và render lại bằng nudge candidate.
- **H. ESP32 qua USB**: Web Serial (mặc định 921600), parse dòng `@MEMORA {...}`, gửi ping / start / next / end / results, xem ảnh BMP trong frame `attempts` và đẩy sang worker. MQTT chuyển lên thanh trên cùng (dùng cho tab 1).

Console log ở cuối trang ghi method, path, status, thời gian của mọi request và frame; không ghi token hay presigned URL.

## Lưu ý an toàn

- Server chỉ bind `127.0.0.1:8090`.
- `X-Memora-Worker-Secret` do server thêm vào, trình duyệt không thấy.
- `/dev/presign` chỉ nhận key bắt đầu bằng `personalization/` và không chứa `..` (URL sống 300 s).
- `/dev/upload-raw` nhận key tường minh (`?key=`) nhưng bắt buộc nằm dưới `personalization/test-ui/`.
- `POST /dev/delete-prefix?prefix=...` chỉ nhận prefix bắt đầu bằng `personalization/test-ui/` (không `..`, tối đa 1024 ký tự) và xóa mọi object bên dưới bằng khóa S3 của worker; trả `{deleted: n}`.
- Dữ liệu test nằm trong bucket Garage dùng chung: dọn prefix `personalization/test-ui/` sau khi thử.
- Route PERSO-009..012 (soundwave initiate/complete, danh sách nguồn, processing) nằm trên nhánh `feat/personalization-source-pipeline` của memora-backend (chưa merge vào `dev`). Chạy backend từ nhánh đó, nếu không các mục E/F trả 404. Ghi âm trên trình duyệt được đổi sang WAV trước khi upload vì backend chỉ nhận WAV/MP3/M4A/AAC.

## Kiểu WAVECODE và xem 3D

**Kiểu hiển thị (tab 2):** mục "Kiểu hiển thị WAVECODE" luôn hiện, tải từ `GET /worker/v1/soundwave/presets` (mỗi thẻ: tên, code, mô tả, decoder, ảnh mẫu; kiểu mặc định chọn sẵn; nút "Tải lại danh sách"; worker lỗi thì hiện thẻ lỗi `code: message`). `style` được gửi trong `soundwave/process`. Sau khi xử lý xong, bấm thẻ kiểu khác để gọi `render-wavecode` với cùng levels, outputPrefix, context và cập nhật ảnh, nên so sánh được các kiểu trên một bản ghi mà không xử lý lại audio.

**Xem 3D (`static/ring3d.js`):** nút "Mở xem 3D" dưới kết quả WAVECODE (tab 2) dựng `BAR_001_SW.glb` bằng three.js 0.147.0 tải từ `cdn.jsdelivr.net` (cần Internet), phủ `wavecode-{style}.svg` của kiểu hiện tại (qua `/dev/object`) lên mesh `ENGRAVE_SURFACE`. Vị trí, kích thước và `mm_per_uv` đọc từ template JSON, không hard-code. Nút: Tô đen (oxi hóa) / Khắc mờ / Ẩn mã, Cận mặt khắc / Toàn nhẫn, Lật ngang / Lật dọc. Tự cập nhật sau khi xử lý hoặc đổi kiểu. Tab 1 có nút "Xem trên SIGNET_001_FP": đặt `fingerprint_alpha.png` (bản được chọn, hoặc lần quét mới nhất đã xong) theo `packages.FINGERPRINT.default` (center, size_mm, rotation_deg) và `uv_origin_mm` / `mm_per_uv_unit`; ảnh được vừa khung size_mm giữ tỉ lệ. Hướng ảnh chưa kiểm bằng mắt, dùng nút Lật nếu ngược.

Route thêm vào server (chỉ GET):

| Route | Bảo vệ |
|---|---|
| `/models/<NAME>.glb` | `NAME` khớp `^[A-Z0-9_]+$`; chỉ đọc file `MODELS_DIR/<NAME>/<NAME>.glb`; `model/gltf-binary`; còn lại 404, không đi ra ngoài `MODELS_DIR` |
| `/models/<NAME>/template.json` | cùng quy tắc, đọc `MODELS_DIR/<NAME>/<NAME>_template.json` |
| `/dev/object?key=` | key phải bắt đầu `personalization/`, tối đa 1024 ký tự, không có `..`, `\`, `//`, nếu không 400; stream object từ bucket với content-type của S3 (cùng origin, không cần CORS) |

**Tái sử dụng và chấp nhận kiểu (tab 2):** thẻ kiểu có nhãn "đã tạo" cho các kiểu có trong `wavecode.styles` của lần phản hồi gần nhất; bấm lại kiểu đã tạo (cùng levels) thì worker dùng lại file (`reused: true`, trang báo "lấy từ bản đã có"). Nút "Chấp nhận kiểu này" gọi `select-wavecode-style`: chỉ giữ kiểu đó, các thẻ khác bị vô hiệu và hiện "Đã chấp nhận kiểu X, đã xóa các kiểu khác". File mỗi kiểu là `wavecode-{style}.svg` / `wavecode-{style}_alpha.png` trong thư mục `selected/{sourceId}/`; đường dẫn hiển thị lấy từ phản hồi.

**Chọn lại đoạn 3 s (tab 2):** khi đã có kết quả và kiểu chưa được chấp nhận, kéo lại cửa sổ trên sóng (hoặc nhập `segmentStartMs`) rồi bấm "Xử lý lại với đoạn mới". Trang xóa prefix `selected/{sourceId}/` qua `/dev/delete-prefix`, gọi lại `soundwave/process` với cùng `rawKey` (không upload lại), `segmentStartMs` mới, kiểu đang chọn và `context`; levels, nhãn "đã tạo", ảnh, danh sách nudge và 3D được làm mới. Bộ đếm "lần chọn lại: n/10" chặn ở 10 như backend; sau "Chấp nhận kiểu này" nút và thanh trượt bị khóa. Nếu worker lỗi sau khi đã xóa thì kết quả cũ mất (trang báo rõ).

**Tải lại từ Garage (tab 2):** nút "Tải lại từ Garage" liệt kê `personalization/test-ui/soundwave/{orderId}/selected/` của đơn giả hiện tại, đọc từng `manifest.json` và hiện bảng (mới nhất trước: thời gian, sourceId, kiểu + cờ đã chấp nhận, `segmentStartMs`, `measuredDurationMs`). Bấm "Chọn" để dựng lại trạng thái từ manifest qua `/dev/object`: levels, kiểu hiện tại, các kiểu đã tạo, `accepted` (khóa giao diện nếu `true`), đoạn, `outputPrefix`, `context`; tải audio gốc từ `request.rawKey` để vẽ lại sóng và cho phép chọn lại đoạn, phát `audio_segment.wav` và cập nhật 3D. Giới hạn: manifest không lưu ứng viên nudge nên danh sách trống, bộ đếm chọn lại bắt đầu lại từ 0.

Route thêm: `GET /dev/list?prefix=` chỉ nhận prefix bắt đầu bằng `personalization/test-ui/`, không có đoạn `..`, không có `\`, tối đa 1024 ký tự (sai thì 400); trả tối đa 1000 key `{key, size, lastModified}` cùng `truncated`, chỉ đọc (list), dùng key S3 của worker.

