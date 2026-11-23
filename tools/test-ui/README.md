# MEMORA Test UI (chỉ dùng local dev)

Một trang web để thử toàn bộ luồng vân tay / soundwave: đăng nhập, đơn hàng, consent, phiên lấy vân tay, nguồn và xử lý, upload soundwave, gọi trực tiếp worker Python và nói chuyện với ESP32-S3 qua USB (Web Serial).

**Không deploy.** Chỉ chạy trên máy dev, không có xác thực riêng.

## Yêu cầu

- API Gateway chạy tại `http://localhost:3000` (prefix `/api/v1`), cùng các service iam / commerce / operations.
- Worker Python chạy tại `http://127.0.0.1:8010` (cho mục G).
- File `services/personalization_worker/.env` có `WORKER_SHARED_SECRET` và `S3_*` (server đọc lúc chạy, không in ra, không gửi cho trình duyệt).
- Chrome hoặc Edge cho Web Serial (mục H) và ghi âm (mục F).

## Chạy

```bash
services/personalization_worker/.venv/Scripts/python tools/test-ui/server.py
```

Mở `http://127.0.0.1:8090`. Chỉ dùng thư viện chuẩn + `boto3`, `python-dotenv` đã có trong venv của worker.

Cấu hình tùy chọn qua biến môi trường hoặc `tools/test-ui/.env` (đã gitignore, xem `.env.example`): `GATEWAY_URL`, `WORKER_URL`, `WORKER_ENV_PATH`, `TEST_UI_PORT`.

## Các mục

- **A. Tài khoản**: hai ô (khách hàng / nhân viên), đăng nhập `POST /auth/login`. Access token lưu trong sessionStorage. Refresh cookie do proxy giữ trong bộ nhớ server theo từng ô (hai tài khoản không đè nhau). Nút Refresh gọi `GET /auth/csrf` rồi `POST /auth/refresh` với `X-CSRF-Token`.
- **B. Đơn hàng**: liệt kê đơn của khách, chọn đơn, xem ring item (template, gói, version). Có ô nhập order id thủ công.
- **C. Consent**: đồng ý (FINGERPRINT_PROCESSING / SOUNDWAVE_PROCESSING, policyVersion mặc định `dev-1`), danh sách, thu hồi.
- **D. Phiên lấy vân tay (nhân viên)**: tạo phiên, tự cập nhật mỗi 2 s, bảng các lần quét có ảnh, nút Lần tiếp theo, Từ chối lần, Chọn lần này, Kết thúc không chọn.
- **E. Nguồn và xử lý**: danh sách nguồn (PERSO-011) và trạng thái xử lý (PERSO-012), tự poll tới READY / PROCESSING_FAILED.
- **F. Soundwave**: chọn file hoặc ghi âm (tối đa 30 s), đo thời lượng, sha256, chọn đoạn 3 s, initiate, PUT thẳng lên Garage theo `uploadUrl` / `uploadHeaders`, complete, rồi theo dõi xử lý.
- **G. Worker**: `/worker/health`, upload ảnh (BMP/PNG) hoặc audio qua `/dev/upload-raw`, gọi `/v1/fingerprint/process` hoặc `/v1/soundwave/process`, xem JSON, PNG alpha kèm hull, SVG, WAVECODE (levels, bars) và render lại bằng nudge candidate.
- **H. ESP32 qua USB**: Web Serial (mặc định 921600), parse dòng `@MEMORA {...}`, gửi ping / start / next / end / results, xem ảnh BMP trong frame `attempts` và đẩy sang worker. Chế độ MQTT không cần mục này.

Console log ở cuối trang ghi method, path, status, thời gian của mọi request và frame; không ghi token hay presigned URL.

## Lưu ý an toàn

- Server chỉ bind `127.0.0.1:8090`.
- `X-Memora-Worker-Secret` do server thêm vào, trình duyệt không thấy.
- `/dev/presign` chỉ nhận key bắt đầu bằng `personalization/` và không chứa `..` (URL sống 300 s).
- `/dev/upload-raw` luôn ép key vào `personalization/test-ui/`.
- Dữ liệu test nằm trong bucket Garage dùng chung: dọn prefix `personalization/test-ui/` sau khi thử.
- Route PERSO-009..012 (soundwave initiate/complete, danh sách nguồn, processing) nằm trên nhánh `feat/personalization-source-pipeline` của memora-backend (chưa merge vào `dev`). Chạy backend từ nhánh đó, nếu không các mục E/F trả 404. Ghi âm trên trình duyệt được đổi sang WAV trước khi upload vì backend chỉ nhận WAV/MP3/M4A/AAC.
