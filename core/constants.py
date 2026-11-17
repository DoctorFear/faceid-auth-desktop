"""
TRUNG TÂM THÔNG SỐ HỆ THỐNG (SINGLE SOURCE OF TRUTH)
Tất cả các ngưỡng AI, góc nghiêng, kích thước khuôn mặt, thời gian chờ và mức bảo mật
đều được định nghĩa duy nhất tại file này để đồng bộ 100% giữa:
- Daemon Server (core/server.py)
- Giao diện Quản trị & Thử nghiệm (gui/modern_gui.py)
- Các Engine AI (fiqa.py, liveness.py, anti_spoof.py, fusion.py)
- Công cụ Đăng ký & Kiểm thử CLI (scripts/enroll.py, scripts/verify_interactive.py)
"""

# ==============================================================================
# 1. KÍCH THƯỚC KHUÔN MẶT & CAMERA
# ==============================================================================
CAMERA_DEFAULT_INDEX = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
MIN_FACE_SIZE_PX = 70  # Kích thước bounding box tối thiểu (px) để xử lý

# ==============================================================================
# 2. ĐÁNH GIÁ CHẤT LƯỢNG ẢNH & TƯ THẾ 3D (FIQA)
# ==============================================================================
FIQA_MIN_SHARPNESS = 35.0
FIQA_MIN_BRIGHTNESS = 35.0
FIQA_MAX_BRIGHTNESS = 235.0
FIQA_MAX_YAW = 25.0
FIQA_MAX_PITCH = 22.0
FIQA_MAX_ROLL = 20.0
FIQA_MOIRE_THRESHOLD = 0.50

# Công thức ước lượng góc Pitch 2D-3D
POSE_YAW_SIGN = -1
POSE_PITCH_SIGN = -1
POSE_PITCH_CENTER_RATIO = 0.42
POSE_PITCH_SCALE = 110.0

# ==============================================================================
# 3. ĐIỀU KIỆN CHÍNH DIỆN & TRỞ VỀ TÂM (FRONTAL & RETURN-TO-CENTER GATING)
# ==============================================================================
STRICT_FRONTAL_MAX_YAW = 15.0    # Độ lệch ngang tối đa được coi là nhìn thẳng (độ)
STRICT_FRONTAL_MAX_PITCH = 15.0  # Độ lệch dọc tối đa được coi là nhìn thẳng (độ)
FRONTAL_STREAK_REQUIRED = 2      # Số frame nhìn thẳng liên tiếp tối thiểu trước khi match

# Độ lệch tối đa so với góc xuất phát (_pose_base) để xác nhận đầu đã quay về nhìn thẳng
POSE_RETURN_MAX_DYAW = 6.0
POSE_RETURN_MAX_DPITCH = 3.8

# ==============================================================================
# 4. THỬ THÁCH CỬ ĐỘNG SỐNG (ACTIVE LIVENESS THRESHOLDS)
# ==============================================================================
# Giới hạn góc tối đa để cho phép khóa mốc xuất phát (_pose_base)
LIVENESS_BASE_MAX_YAW = 15.0
LIVENESS_BASE_MAX_PITCH = 16.0
LIVENESS_BASE_CALIB_FRAMES = 4  # Số frame lấy trung vị ổn định góc gốc trước khi bắt đầu

# Ngưỡng biên độ cử động (Tính thuần túy theo độ lệch tương đối Delta so với góc gốc)
LIVENESS_TURN_LEFT_DYAW = -9.5
LIVENESS_TURN_LEFT_ABS_YAW = -999.0  # Vô hiệu hóa ngưỡng tuyệt đối để chống tự pass giả

LIVENESS_TURN_RIGHT_DYAW = 9.5
LIVENESS_TURN_RIGHT_ABS_YAW = 999.0

LIVENESS_NOD_DOWN_DPITCH = -5.0
LIVENESS_NOD_DOWN_ABS_PITCH = -999.0

LIVENESS_LOOK_UP_DPITCH = 5.0
LIVENESS_LOOK_UP_ABS_PITCH = 999.0

LIVENESS_POSE_HOLD_FRAMES = 2  # Số frame giữ tư thế để xác nhận cử động

# ==============================================================================
# 5. CHỐNG GIẢ MẠO THỤ ĐỘNG & ĐỐI KHỚP ĐA PHƯƠNG THỨC (PAD & FUSION)
# ==============================================================================
PAD_REAL_THRESHOLD = 0.60
PAD_SCALE = 2.7
PAD_SMOOTH_WINDOW = 5

# Trọng số phạt mềm khi phát hiện vân Moiré màn hình hoặc FIQA thấp
MOIRE_PASSIVE_PENALTY = 0.82
MOIRE_FIQA_PENALTY = 0.85
LOW_FIQA_PENALTY = 0.40

# Trọng số kết hợp Top-2 Templates của cùng 1 người dùng
TOP1_TEMPLATE_WEIGHT = 0.70
TOP2_TEMPLATE_WEIGHT = 0.30
MAX_PROTOTYPES_PER_USER = 5

# Ngưỡng nhận diện mặc định (Mức 1: Cân bằng)
DEFAULT_SIM_THRESHOLD = 0.62
DEFAULT_DECISION_THRESHOLD = 0.68

# Bảng cấu hình 3 mức độ bảo mật trên giao diện Quản trị
SECURITY_LEVEL_THRESHOLDS = {
    0: {"sim_threshold": 0.58, "decision_threshold": 0.64},  # Linh hoạt (Nhanh)
    1: {"sim_threshold": 0.62, "decision_threshold": 0.68},  # Cân bằng (Khuyên dùng)
    2: {"sim_threshold": 0.66, "decision_threshold": 0.74},  # Nghiêm ngặt (An toàn cao)
}

# ==============================================================================
# 6. THỜI GIAN CHỜ & CHỐNG QUÉT VÉT CẠN (TIMEOUTS & RATE LIMITING)
# ==============================================================================
DEFAULT_VERIFY_TIMEOUT_SEC = 8.0
DEFAULT_CHALLENGE_TIMEOUT_SEC = 16.0
POST_LIVENESS_WINDOW_SEC = 5.0       # Thời gian gia hạn để quay mặt về nhìn thẳng sau cử động
DRILL_FAST_COUNTDOWN_SEC = 3.0       # Đếm ngược chuẩn bị ở Chế độ 2 (Mở khóa nhanh)
DRILL_FAST_SCAN_WINDOW_SEC = 4.5     # Thời gian quét liên tục trước khi chốt từ chối ở Chế độ 2
INTERACTIVE_SCAN_WINDOW_SEC = 7.0    # Thời gian quét sau khi hoàn thành cử động ở Chế độ 3

DEFAULT_MAX_FAILURES = 3
DEFAULT_LOCKOUT_SEC = 45.0
DEFAULT_FLOOD_MAX_REQ_PER_SEC = 5
