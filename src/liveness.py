import numpy as np

def calculate_ear(eye_landmarks):
    """
    Tính Eye Aspect Ratio (EAR) cho một mắt gồm 6 điểm mốc:
      p1: khóe mắt ngoài (index 0)
      p2, p3: mí mắt trên (index 1, 2)
      p4: khóe mắt trong (index 3)
      p5, p6: mí mắt dưới (index 4, 5)
    Công thức: EAR = (|p2 - p6| + |p3 - p5|) / (2 * |p1 - p4|)
    """
    pts = np.array(eye_landmarks, dtype=np.float64)
    dist_v1 = np.linalg.norm(pts[1] - pts[5])
    dist_v2 = np.linalg.norm(pts[2] - pts[4])
    dist_h = np.linalg.norm(pts[0] - pts[3])

    if dist_h == 0:
        return 0.0

    ear = (dist_v1 + dist_v2) / (2.0 * dist_h)
    return float(ear)

class BlinkDetector:
    def __init__(self, ear_threshold=0.21, consecutive_frames=2):
        self.ear_threshold = ear_threshold
        self.consecutive_frames = consecutive_frames
        self.blink_counter = 0
        self.closed_frame_count = 0
        self.state = "OPEN"

    def update(self, landmarks):
        """
        landmarks: dict từ face_recognition.face_landmarks
        Trả về: (is_blinked_this_frame, avg_ear)
        """
        if "left_eye" not in landmarks or "right_eye" not in landmarks:
            return False, 0.0

        left_ear = calculate_ear(landmarks["left_eye"])
        right_ear = calculate_ear(landmarks["right_eye"])
        avg_ear = (left_ear + right_ear) / 2.0

        is_blinked = False

        if avg_ear < self.ear_threshold:
            self.closed_frame_count += 1
            if self.closed_frame_count >= self.consecutive_frames:
                self.state = "CLOSED"
        else:
            # Mắt mở lại sau khi đã nhắm đủ số frame quy định
            if self.state == "CLOSED":
                self.blink_counter += 1
                is_blinked = True
            self.state = "OPEN"
            self.closed_frame_count = 0

        return is_blinked, avg_ear

    def reset(self):
        self.blink_counter = 0
        self.closed_frame_count = 0
        self.state = "OPEN"
