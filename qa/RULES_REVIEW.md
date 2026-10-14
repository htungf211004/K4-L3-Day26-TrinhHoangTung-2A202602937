# Bản lịch sử: đối chiếu trước khi sửa lỗi công tố

**Đã được thay thế bởi [FINAL_PREPARATION.md](FINAL_PREPARATION.md).** Lỗi `course_day` dưới đây đã được sửa; bundle mới đã loại test/tài liệu phụ và được kiểm tra lại.

Ngày: 14/10/2026, Asia/Saigon. Đội cá nhân: `TrinhHoangTung-2A202602937`.

## Kết luận

Chưa phát hiện import bị cấm, sửa thư mục được bảo vệ, I/O trong hai entry point trên các mẫu đã chạy, hoặc claim vi phạm schema/quota. Có **một lỗi độ bền đã tái hiện trong công tố** và các giới hạn xác minh cần giữ rõ. Checklist chuẩn bị nộp 6/6 trong `QA_FINAL.md` không có nghĩa mọi yêu cầu đấu thật đã được chứng minh.

Lần rà soát này chỉ thêm công cụ/báo cáo QA trong `submissions/`; không sửa `agent/`, `deck/`, `eval/` hay bundle đã nộp vào thư mục chuẩn bị.

## Phát hiện cần xử lý

**Mức vừa — công tố bị ngắt khi đáp án có course_day không chuyển được sang số.**

Tại `eval/prosecute.py:889`, `_hook_wrong_answer` gọi `int(ans_day)` ngoài khối bắt lỗi. `prosecute()` cũng không bắt ngoại lệ từ việc gọi hook. Tái hiện bằng fixture công khai `wrong_answer__near_miss`, chỉ thay `answer.course_day` thành chuỗi `"unknown"`: nhận `ValueError: invalid literal for int() with base 10: 'unknown'`.

Đây là đầu vào tự tạo từ fixture công khai, không phải trace của đội khác. Đáp án sai kiểu vẫn có thể là JSON hợp lệ; công tố nên chịu được lỗi của bên phòng thủ. Ngoại lệ làm hàm không trả về tập claim, có thể làm mất toàn bộ lượt công tố. `RULES.md` §4 không ghi riêng rằng một ngoại lệ công tố tự động dẫn tới loại bài, nên không kết luận lỗi này là căn cứ bị loại.

Hướng sửa: xác thực/chuyển `course_day` an toàn một lần; nếu không phải ngày hợp lệ, bỏ qua phép so sánh số của detector này để những detector khác vẫn chạy. Cần kiểm tra các giá trị `"unknown"`, list, dict và `null`, rồi chạy lại kiểm tra công tố và đóng gói lại nếu sửa. Chưa sửa mã trong lần review này.

## Đối chiếu từng mục

| Điều khoản | Kết quả và phạm vi bằng chứng |
| --- | --- |
| §1: quyền sửa và file được bảo vệ | Git Windows sạch cho `kit/`, `bots/`, `fixtures/`; Git WSL cũng sạch khi dùng cùng thiết lập CRLF/filemode. ZIP world có checksum đúng; 7 file world khớp từng byte bản gốc. Không chỉnh các thư mục được bảo vệ. |
| §2: thư viện chuẩn và danh sách cấm | Kiểm tra AST 9 file Python thuộc ba thư mục: chỉ stdlib và module dự án; không thấy import/call bị cấm, kể cả alias và tiền tố `os.exec*`/`os.spawn*`. Script QA ở `submissions/` không nằm trong bundle. |
| §3: Gateway.decide | Hàm đồng bộ; không gọi tool/execute trực tiếp. 444 lần gọi qua bộ regression trả `Decision` hợp lệ, serialize được; không có sự kiện I/O/process/network bị ghi nhận bên trong entry point. Thời gian lớn nhất đo được 0,110 ms, dưới 250 ms. Đây là phép đo trên mẫu local, không phải bảo đảm với mọi input hay tải máy. |
| §4: prosecute | 40 fixture công khai: 34 claim verified, 0 false/unproven/rejected, 0 error/timeout. Tất cả output đúng schema, ≤4 claim và ≤1 claim/family; không ghi nhận I/O trong entry point. Thời gian lớn nhất 0,589 ms, dưới 5 s. Ngoài tập fixture chuẩn có lỗi robustness đã mô tả trên. |
| §5: deck | 14 lá: 10 attack + 4 blank; MCP 4, A2A 4, gateway 2; đủ 9 class. Lineup 10 lá không lặp. Replica flip chỉ vào path đã đo drift `7a8d8046`. Validate world thật đạt 0 FAIL, 5 WARN. Lethality band chưa được chứng minh bằng live Arena. |
| §6: các hành vi điểm 0 | Gateway cho phép đọc hợp lệ và signed A2A hợp lệ, không deny toàn bộ; routing dùng header, cấm body route; write cần provenance, If-Match, idempotency key và chặn ghi lặp. Guardrail có test trả lời câu hỏi sạch từ nguồn thật và abstain khi thiếu chứng cứ; claim chỉ chứa một class. Không thấy truy cập thư mục đội khác trong code được rà soát. Không chạy probe sandbox ở lần kiểm tra này. |
| §7: hợp tác | Chỉ dùng mã, bot và fixture công khai của kit trong review; không đọc trace hoặc source đội khác. Không thể suy ra lịch sử hợp tác ngoài workspace chỉ từ code. |
| §8: bundle và khóa bài | Bundle đúng tên đội, có 16 file payload, manifest và 39 hash Python của kit; hash khớp mã hiện tại và bản sao trong thư mục chuẩn bị nộp. TEAMMATES đầy đủ tên/MSSV. Giờ bắt đầu lớp và nơi nộp chưa có dữ liệu để xác nhận việc nộp đúng hạn. |

## Những giới hạn không được gọi là đã pass

1. **Lethality band.** `validate_deck.py` dùng proxy local và tự in cảnh báo không chứng minh đấu thật. Bốn cảnh báo `held-in-principle` thuộc `atk_03`, `atk_04`, `atk_05`, `atk_09`. `spar.py` hiện dùng kế hoạch tool cố định và đáp án tổng hợp; chạy spar cũng không thay thế chứng minh đầy đủ từ Arena thật.
2. **Kernel sandbox.** Test công khai trước đó có 7 skip, trong đó 3 cần macOS `sandbox-exec`; không khẳng định đã kiểm chứng ranh giới kernel trên Windows/WSL.
3. **I/O lúc import.** `eval/prosecute.py:653-670` đọc `kit/world/*/drift.json` lúc import; `load_fixtures()` là hàm đọc file phục vụ local evaluation. Hai thao tác này không chạy trong `prosecute()` khi kiểm tra entry point. Nếu giảng viên áp dụng cấm I/O cho toàn bộ quá trình import artifact, thay vì chỉ hàm chấm, cần xử lý riêng. Code hiện tại có ghi rõ thiết kế này.
4. **Hash trong kit.submit.** Dù §8 diễn đạt “every kit/ file”, bản công cụ được cung cấp chỉ ghi hash `*.py` (39 file). Đã giữ nguyên công cụ; tính toàn vẹn world kiểm tra riêng. Không tự mở rộng manifest khác định dạng kit.

## Bằng chứng

- Lần kiểm tra công khai trước: `make test` — 4609 passed, 7 skipped; `make validate` — 0 FAIL, 5 WARN; `make submit TEAM=TrinhHoangTung-2A202602937` thành công.
- Lần review này: `.venv/bin/python submissions/audit_rules.py` — 66 regression test pass; đo runtime và quota trên các mẫu; ghi kết quả vào `submissions/RULES_AUDIT.json`.
- `submissions/qa_final.py --bundle` kiểm tra import, protected directories, world ZIP, CRC, danh sách file, manifest và SHA-256.
- SHA-256 bundle giữ nguyên:

```text
7ed57411c69ceb25d7a99b60436d5476efea38f42624e4725478fe493db5f6eb
```
