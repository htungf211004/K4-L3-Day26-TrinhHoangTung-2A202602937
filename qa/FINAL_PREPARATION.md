# Bản cuối chuẩn bị nộp — K4 Day 26

Ngày: **14/10/2026**, Asia/Saigon.

Đội cá nhân: **TrinhHoangTung-2A202602937**. Thành viên: **Trịnh Hoàng Tùng — MSSV 2A202602937**.

Đã đọc toàn bộ hướng dẫn lab được gửi kèm, README/RULES ở root, cùng README của agent/deck/eval. Báo cáo này thay thế kết luận của các bản QA/review trước khi sửa lỗi và dọn bundle.

## Kết quả cuối: checklist chuẩn bị nộp 6/6

- [x] `make test`: **4609 passed, 7 skipped**, exit code 0; G-KEY PASS, 108 file được quét, 0 vi phạm.
- [x] `make validate`: dùng world thật `df8c55dabb35`, **0 FAIL, 5 WARN**, exit code 0.
- [x] `git status --short -- kit/ bots/ fixtures/` không có thay đổi. Không sửa công cụ hay dữ liệu được bảo vệ.
- [x] AST audit của **7 file Python** trong payload: chỉ stdlib và module dự án; không thấy import/call bị cấm. Hai entry point kiểm tra local không ghi nhận I/O/process/network.
- [x] `make submit TEAM=TrinhHoangTung-2A202602937` tạo bundle thành công.
- [x] Thư mục nộp có đúng **2 file**, tên đội thống nhất và TEAMMATES đầy đủ.

## Thư mục cần gửi

```text
K4-DAY26-TrinhHoangTung-2A202602937/
├── TEAMMATES.md
└── TrinhHoangTung-2A202602937.bundle
```

Không cần gửi toàn bộ repository hay thư mục `qa/`.

Bundle cuối **67.922 bytes**, gồm đúng **10 file payload** và `MANIFEST.json`:

```text
agent/__init__.py
agent/gateway.py
agent/guardrails.py
agent/strategy.py
agent/telemetry.py
agent/prompt.md
deck/deck.json
deck/lineup.json
eval/__init__.py
eval/prosecute.py
MANIFEST.json
```

Đã loại khỏi payload: `agent/test_gateway.py`, `agent/test_guardrails.py`, `agent/README.md`, `agent/GATEWAY.md`, `deck/README.md`, `eval/README.md`. Test/tài liệu được chuyển sang `qa/` để lưu local; không mất bản gốc. Script, báo cáo QA, log spar, world, thư viện môi trường và cache không nằm trong bundle. Giữ nguyên hạ tầng source cần để tái chạy kiểm tra.

Manifest có tên đội đúng, hash của 10 file khớp file nguồn và ZIP; 39 hash Python của kit khớp cơ chế công cụ được cung cấp. CRC ZIP hợp lệ. Bản sao trong thư mục nộp khớp từng byte với bản trong `submissions/`.

**SHA-256 bundle cuối:**

```text
e2dbc27270883137ff32f36b2b1d537d1b6cb68d701c9dcbfcbe6a8a0293a329
```

## Sửa lỗi và kiểm tra bổ sung

- Đã sửa lỗi công tố ném `ValueError` khi `answer.course_day` không chuyển được sang số. Chỉ so sánh ngày có kiểu int/string chuyển đổi được; không cắt float hay coi bool là ngày. Dữ liệu ngày lỗi không ngắt các detector/kiểm tra track còn lại.
- **69 test riêng pass**: gateway, guardrail, ba test robustness mới. Đã kiểm tra `unknown`, list, dict, null, bool, float, infinity và chuỗi số quá dài; ngày sai hợp lệ vẫn bị cáo buộc với đúng evidence, track sai vẫn được phát hiện dù ngày lỗi.
- **40 fixture công tố công khai**: 34 verified, 0 false/unproven/rejected/error/timeout; precision/recall/F1 = 1.000 trên tập local này.
- Audit entry point: 444 lần gọi `decide`, thời gian lớn nhất **0,085 ms**; 40 lần gọi `prosecute`, thời gian lớn nhất **1,076 ms**; không ghi nhận sự kiện I/O. Đây là số đo trên các mẫu local.
- Kiểm tra import trực tiếp **từ bundle sealed**, không dùng bản agent/eval bên ngoài ZIP: các module cần thiết import thành công; smoke gateway/guardrail/prosecutor pass.
- World ZIP có checksum đúng và 7 file export khớp bản gốc, không có `truth.json`.

## Đấu tập theo hướng dẫn

| Lệnh | Kết quả local, seed 1 |
| --- | --- |
| `make spar BOT=rookie AS=all` | YOU 100–0 rookie; MISSED còn protocol_misuse ×3. |
| `make spar BOT=operator AS=all` | YOU 100–0 operator; MISSED còn protocol_misuse ×2. |
| `make spar BOT=adversary AS=prosecutor` | YOU 21–0 adversary; MISSED còn protocol_misuse ×2; không có dòng cáo buộc FALSE trong log. |
| `make spar BOT=rookie AS=attacker` | DRAW 100–100; role attacker dùng prosecutor của bot làm baseline nên chưa khai thác các lỗi được ghi trong MISSED. |

Kết quả spar là phép tập local theo kit; không thay thế kết quả live Arena. MISSED có thể còn lại do giới hạn một claim/family mỗi exchange; không tự suy ra mọi MISSED đều là thiếu detector.

## Giới hạn còn lại

- **5 WARN** của validator: cảnh báo proxy lethality và 4 `held-in-principle` cho atk_03/04/05/09; không có FAIL. Lethality band chưa được chứng minh đầy đủ bằng live Arena.
- **7 skip** của public tests: 2 cần corpus nguồn `.tex`, 2 cần corpus repo Arena của giảng viên, 3 cần macOS `sandbox-exec`. Không khẳng định đã kiểm chứng sandbox kernel trên Windows/WSL.
- `prosecute()` không I/O trong audit; module vẫn nạp dữ liệu drift lúc import, như thiết kế đã nêu trong review.
- Công cụ kit.submit được cung cấp chỉ hash `*.py` trong kit; không chỉnh công cụ hay tự thay đổi định dạng manifest.
- Nơi nộp vẫn ghi **TODO — cần giảng viên xác nhận** trong đề. Chưa gửi bài lên hệ thống; `agent/` và `deck/` khóa từ lúc buổi học bắt đầu.

## Bằng chứng local

`qa/make-test.log`, `qa/make-validate.log`, `qa/make-submit.log`, `qa/local-tests.log`, `qa/final-audit.log`, `qa/verify-delivery.log`, `qa/RULES_AUDIT.json`, các log `qa/spar-*.log`.
