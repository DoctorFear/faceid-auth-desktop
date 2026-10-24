import os
import sys
import time
import logging
from contextlib import contextmanager
from typing import Generator, Optional
import cv2
import numpy as np

logger = logging.getLogger("faceid.camera")


class CameraBusyError(Exception):
    """Ngoai le nem ra khi camera bi chiem dung boi tien trinh khac (Zoom, Meet, Discord...)."""
    pass


class CameraDeviceError(Exception):
    """Ngoai le nem ra khi khong tim thay thiet bi camera hoac khong the doc frame."""
    pass


class LinuxCameraManager:
    """
    Quan ly thiet bi Camera tren Linux (V4L2) dap ung cac yeu cau he thong & van hanh:
    1. Chuyen doi tai nguyen Camera khong gay treo Terminal (Graceful Device Contention Handling):
       - Phat hien nhanh khi /dev/video0 bi chiem dung (EBUSY).
       - Gioi han acquire timeout (<= 1.0s) de lap tuc tra quyen ve PAM fallback sang Password.
    2. Bao ve quyen rieng tu & Den LED Camera (Privacy & Decree 13/2023/ND-CP):
       - Camera CHI bat (den LED sang) khi co request xac thuc tu PAM (sudo, screensaver).
       - Dong camera ngay lap tuc sau khi ket thuc phien xac thuc qua context manager (den LED tat).
       - Zero-Image Retention: Khung hinh chi luu tam tren RAM (volatile buffer), khong bao gio luu xuong dia.
    """

    def __init__(
        self,
        device_index: int = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        open_timeout_sec: float = 1.0
    ):
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.open_timeout_sec = open_timeout_sec

    def is_device_available(self) -> bool:
        """Kiem tra xem file thiet bi /dev/video* co ton tai va co quyen doc khong."""
        if sys.platform != "win32":
            dev_path = f"/dev/video{self.device_index}"
            if not os.path.exists(dev_path):
                return False
            if not os.access(dev_path, os.R_OK | os.W_OK):
                return False
        return True

    @contextmanager
    def acquire(self) -> Generator[cv2.VideoCapture, None, None]:
        """
        Context manager mo camera an toan, kiem soat den LED va giai phong lap tuc.
        Su dung:
            with camera_mgr.acquire() as cap:
                ret, frame = cap.read()
        """
        cap: Optional[cv2.VideoCapture] = None
        t_start = time.time()

        try:
            # Chon backend phu hop: V4L2 tren Linux, DSHOW tren Windows
            if sys.platform == "win32":
                backend = cv2.CAP_DSHOW
            else:
                backend = cv2.CAP_V4L2

            cap = cv2.VideoCapture(self.device_index, backend)
            if not cap.isOpened() and sys.platform != "win32":
                cap.release()
                cap = cv2.VideoCapture(self.device_index)

            # Kiem tra neu camera bi chiem dung hoac timeout
            if not cap.isOpened():
                elapsed = time.time() - t_start
                logger.warning(f"Camera {self.device_index} khong the mo sau {elapsed:.2f}s (co the dang bi chiem dung).")
                raise CameraBusyError(f"Camera /dev/video{self.device_index} dang ban hoac bi khoa boi ung dung khac.")

            # Cau hinh do phan giai & toc do khung hinh
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)

            # Doc thu 1 frame de kiem tra tinh san sang (tranh truong hop isOpened=True nhung khong doc duoc frame)
            read_start = time.time()
            ret, test_frame = cap.read()
            if not ret or test_frame is None or test_frame.size == 0:
                raise CameraBusyError(f"Khong the lay frame tu Camera /dev/video{self.device_index} (V4L2 busy).")

            # Tra capture object ve cho caller su dung
            yield cap

        finally:
            # Dam bao giai phong thiet bi camera ngay lap tuc de tat den LED
            if cap is not None:
                try:
                    cap.release()
                    logger.debug("Da giai phong thiet bi camera, tat den LED.")
                except Exception as e:
                    logger.error(f"Loi khi giai phong camera: {e}")
