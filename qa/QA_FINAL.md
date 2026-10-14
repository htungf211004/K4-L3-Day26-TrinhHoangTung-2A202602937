# Bản lịch sử: kiểm tra trước khi dọn payload — Day 26

**Đã được thay thế bởi [FINAL_PREPARATION.md](FINAL_PREPARATION.md).** Bundle/hash và lỗi chưa sửa nêu dưới đây thuộc phiên bản trước.

Ngày kiểm tra: 14/10/2026 (Asia/Saigon).

Rà soát toàn bộ điều khoản sau checklist: xem `RULES_REVIEW.md` và `RULES_AUDIT.json`. Có lỗi robustness công tố với `course_day: "unknown"`; checklist 6/6 bên dưới là checklist chuẩn bị nộp, không chứng minh toàn bộ hành vi đấu thật.

Tên đội: `TrinhHoangTung-2A202602937` — bài làm cá nhân.

Thành viên: **Trịnh Hoàng Tùng**, MSSV **2A202602937**.

## Checklist: 6/6

- [x] `make test`: **4609 passed, 7 skipped**, exit code 0; G-KEY PASS, 0 violations.
- [x] `make validate` trên world thật `kit/world/df8c55dabb35/`: **0 FAIL, 5 WARN**, exit code 0. Kết quả được kiểm tra lại bởi `make submit`.
- [x] `git status --short -- kit/ bots/ fixtures/`: không có thay đổi. Khi chạy Git trong WSL, dùng `-c core.autocrlf=true -c core.filemode=false` để khớp cấu hình checkout Windows; không sửa cấu hình Git hay file được bảo vệ.
- [x] Đã kiểm tra AST của **9 file Python** trong `agent/`, `deck/`, `eval/`: chỉ thư viện chuẩn và module của dự án; không có import/call bị cấm được phát hiện, gồm kiểm tra alias và tiền tố `os.exec*`/`os.spawn*`.
- [x] `make submit TEAM=TrinhHoangTung-2A202602937` tạo thành công `submissions/TrinhHoangTung-2A202602937.bundle`.
- [x] Thư mục `K4-DAY26-TrinhHoangTung-2A202602937/` có đúng `TEAMMATES.md` và `TrinhHoangTung-2A202602937.bundle`; thông tin cá nhân đầy đủ.

## Kiểm tra bổ sung

- `.venv/bin/python -m unittest agent.test_gateway agent.test_guardrails -q`: **66 tests, OK**.
- ZIP world gốc có SHA-256 đúng với file `.sha256`; **7 file** world đã giải nén khớp từng byte với ZIP; không có `truth.json`.
- Bundle: **96.205 bytes**, **16 file payload**; ZIP không lỗi CRC; danh sách file và SHA-256 trong manifest khớp nội dung bundle và file nguồn hiện tại.
- Manifest có tên đội đúng và **39 hash file Python trong `kit/`**, đúng cơ chế của `kit.submit` được cung cấp. Công cụ này chỉ ghi hash `*.py`; kiểm tra world được thực hiện riêng, không sửa công cụ.
- Bản bundle trong thư mục nộp có cùng SHA-256 với bản trong `submissions/`:

```text
7ed57411c69ceb25d7a99b60436d5476efea38f42624e4725478fe493db5f6eb
```

- Metadata `team` trong `deck/deck.json` đã được đồng bộ với tên đội; không thay đổi nội dung lá bài hay lineup.

## Giới hạn đã biết

**7 test skipped**: 2 test cần corpus nguồn `.tex`, 2 test cần corpus trong repo Arena của giảng viên, 3 test cần macOS `sandbox-exec`. World export của sinh viên vẫn được validate riêng và đã pass. Không khẳng định đã xác minh sandbox kernel trên máy Windows/WSL này.

**5 WARN của validator**: 1 cảnh báo chung rằng kit không có live mutation engine của Arena; 4 cảnh báo `R8-held-in-principle` cho `atk_03`, `atk_04`, `atk_05`, `atk_09`, do proxy chỉ xác minh mẫu `gateway.denied`. Các defense event tương ứng là `registry.pinned`, `guardrail.refused`, `gateway.budget_held`, `a2a.crosschecked`; đây là các kiểu phòng thủ được mô tả trong bot công khai của kit. Không khẳng định đã chứng minh lethality band qua đấu thật.

## Trạng thái nộp

**Đã chuẩn bị thư mục nộp; chưa gửi lên hệ thống.** Nơi nộp vẫn là `TODO — cần xác nhận` trong hướng dẫn. Cần dùng nơi nộp và quy định số thành viên do giảng viên thông báo; bundle đã dùng tên đội cá nhân được người dùng xác nhận.

`agent/` và `deck/` khóa từ lúc buổi học bắt đầu. Nếu tiếp tục sửa `eval/` trước khi gửi bản mới, cần chạy lại kiểm tra liên quan và đóng gói lại bundle.
