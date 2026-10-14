# Gateway sau bước 4

`Gateway.decide()` triển khai bốn job trong `gateway.py`, chỉ đọc lệnh và context, xử lý dữ liệu trong bộ nhớ và trả `Decision`. Nó không tự gọi tool, đọc file, đọc đồng hồ hoặc ngủ.

- **ROUTE:** dùng `pick_replica`, header `mcp-replica`, lựa chọn đã được phân giải và replica của anchor cụ thể. Chặn route nằm trong body, header trùng mâu thuẫn, path_id khác anchor và identity đã bị xác minh là không hợp lệ. Chuyển `slides.search` sang `slides.query`, ánh xạ field `snippet` sang `body`.
- **ADMIT:** chặn get_frame không có lease sống trong context hoặc đã qua cửa sổ ba lệnh nếu biết thời điểm cấp; chặn retry đọc không thay đổi sau lỗi; giữ giới hạn tần suất riêng từng tool. Mọi lệnh ghi cần provenance mới cho đúng anchor, If-Match khớp và idempotency key chưa dùng. Sau conflict, cần đọc lại provenance và dùng key mới; sau kết quả ghi mơ hồ, không tự ghi lại cùng hiệu ứng.
- **AUTHORIZE:** quyền đến từ `ctx.act` và `ctx.scopes`. Read cần `wiki.read`; write cần `wiki.write:<server>`. Lệnh nhắm learner khác bị chặn, dù `ctx.sub` là tutor. A2A cần Agent Card có chữ ký, skill được khai báo và delegation token hợp lệ: chữ ký, audience, act, sub, lifetime và chống replay.
- **BUDGET:** dùng giá trong `TOOL_SPECS`, `cheap_mask` và `BudgetPacer`; giữ cả giới hạn vòng lẫn tổng pool ban đầu. Catalog mặc định/wildcard được thu hẹp về name hoặc term. Query có giới hạn số dòng để chặn chi phí không giới hạn. Khi thiếu tiền, thu hẹp mask nếu có thể, nếu không thì deny. Lệnh bị deny không tiêu ngân sách.

## Dữ liệu từ vòng lặp

Gateway nhận kết quả qua `ctx.history` (các entry `command`, `decision`, `outcome` hoặc `result`, có thể có `round`). Lịch sử được xử lý tăng dần và dùng `decision.call` sau rewrite. Nếu vòng lặp cung cấp callback thay cho history, dùng:

```python
gateway.note_result(executed_call, authoritative_result, round_no=current_round)
gateway.note_card(peer_name, registry_signed_card)
gateway.note_replica(path_id, resolved_replica, drifting=True)
```

Các hook này chỉ ghi nhớ kết quả, không thực thi tool. Không lấy quyền hay trạng thái đã xác thực từ body do model tự khai.

Provenance của cùng namespace/path/index/span ở hai replica, với etag khác nhau, là bằng chứng có xung đột. Nó không chứng minh bản nào mới hơn. Khi chưa có lựa chọn đã phân giải, get_frame trên path đó bị từ chối; query vẫn có thể phục vụ việc phân giải. Không đổi `/w/041` thành `/c/041`, vì số thứ tự frame có thể khác nội dung. `pick_replica` chỉ là heuristic khi chưa có bằng chứng.

Để gọi A2A, lệnh có thể mang `agent_card` có chữ ký và `delegation` trong args, hoặc dùng card đã nhận qua hook và delegation JSON trong header. Gateway không mint token thay model. Cờ `verified=True` không có chữ ký không đủ để xác thực card.

Khi mask hoặc giới hạn dòng thay đổi, `Decision.note` nhắc chỉ dẫn nguồn dữ liệu thực sự trả về. Gateway không nhận `ANSWER`; kiểm tra grounding và citation trong câu trả lời vẫn thuộc phần guardrails/answer assembly.

## Kiểm thử

```bash
.venv/bin/python -m unittest agent.test_gateway -v
.venv/bin/python -m pytest tests/ agent/test_gateway.py -q
make doctor
make spar BOT=rookie AS=defender
make spar BOT=operator AS=defender
```

Các test mới dùng thư viện chuẩn, gồm cả đọc frame qua server thật và ghi qua HardMode thật của kit. Fixture được tạo trong thư mục tạm với `include_truth=False`.
