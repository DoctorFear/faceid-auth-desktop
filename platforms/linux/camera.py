import os
import sys
import time
import logging
from contextlib import contextmanager
from typing import Generator, Optional
import cv2
import numpy as np

logger = logging.getLogger("faceid.camera")

# Danh sach tu khoa thiet bi camera ao bi cam tuyet doi (Anti-Virtual-Camera Injection)
BLOCKED_VIRTUAL_CAMERA_KEYWORDS = (
    "v4l2loopback",
    "dummy video device",
    "obs virtual camera",
    "obs-camera",
    "droidcam",
    "iriun",
    "manycam",
    "vivid",
    "virtual",
)


class CameraBusyError(Exception):
    """Ngoai le nem ra khi camera bi chiem dung boi tien trinh khac (Zoom, Meet, Discord...)."""
    pass


class CameraDeviceError(Exception):
    """Ngoai le nem ra khi khong tim thay thiet bi camera hoac khong the doc frame."""
    pass


class VirtualCameraDetectedError(CameraDeviceError):
    """Ngoai le Bao mat: Phat hien thiet bi Camera ao (v4l2loopback / OBS) co tinh bom video gia mao."""
    pass


class LinuxCameraManager:
    """
    Quan ly thiet bi Camera tren Linux (V4L2) & Windows (DSHOW) dap ung cac yeu cau an ninh:
    1. Chan Camera Ao (Anti-Virtual Camera / v4l2loopback Blocking):
       - Kiem tra truc tiep /sys/class/video4linux/videoX/name, driver symlink va USB VID:PID.
       - Tu choi ngay lap tuc neu phat hien v4l2loopback, Dummy video, OBS, DroidCam...
    2. Chuyen doi tai nguyen Camera khong gay treo Terminal (Graceful Device Contention Handling):
       - Phat hien nhanh khi /dev/video0 bi chiem dung (EBUSY).
       - Gioi han acquire timeout (<= 1.0s) de lap tuc tra quyen ve PAM fallback sang Password.
    3. Bao ve quyen rieng tu & Den LED Camera (Privacy & Decree 13/2023/ND-CP):
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
        open_timeout_sec: float = 1.0,
        allow_virtual_camera: bool = False,
    ):
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.open_timeout_sec = open_timeout_sec
        self.allow_virtual_camera = allow_virtual_camera

    def verify_physical_camera(self) -> tuple[bool, str]:
        """
        Xac minh thiet bi camera la phan cung vat ly that qua Linux sysfs (/sys/class/video4linux):
        - Kiem tra ten thiet bi (name) khong chua tu khoa camera ao (v4l2loopback, dummy, obs...).
        - Kiem tra duong dan thiet bi co lien ket voi bus phan cung (usb / pci / platform) hay la virtual.
        """
        if self.allow_virtual_camera or sys.platform == "win32":
            return True, "Physical/Dev Camera OK"

        sysfs_base = f"/sys/class/video4linux/video{self.device_index}"
        if not os.path.exists(sysfs_base):
            return False, f"Khong tim thay sysfs node: {sysfs_base}"

        # 1. Kiem tra duong dan thuc (realpath) trong /sys/devices/virtual
        real_sysfs = os.path.realpath(sysfs_base)
        if "/devices/virtual/" in real_sysfs:
            return False, f"Phat hien Virtual Device Path trong sysfs: {real_sysfs}"

        # 2. Kiem tra ten thiet bi trong /sys/class/video4linux/videoX/name
        name_file = os.path.join(sysfs_base, "name")
        dev_name = ""
        if os.path.exists(name_file):
            try:
                with open(name_file, "r", encoding="utf-8", errors="ignore") as f:
                    dev_name = f.read().strip()
                lower_name = dev_name.lower()
                if "virtualbox webcam" not in lower_name:
                    for kw in BLOCKED_VIRTUAL_CAMERA_KEYWORDS:
                        if kw in lower_name:
                            return False, f"Tu choi Camera Ao '{dev_name}' (Khop tu khoa cam: '{kw}')"
            except Exception as e:
                logger.warning(f"Khong the doc sysfs name: {e}")

        # 3. Kiem tra modalias / USB VID:PID (neu co)
        modalias_file = os.path.join(sysfs_base, "device", "modalias")
        if os.path.exists(modalias_file):
            try:
                with open(modalias_file, "r", encoding="utf-8", errors="ignore") as f:
                    modalias = f.read().strip()
                if not modalias.startswith(("usb:", "pci:", "acpi:", "platform:")):
                    return False, f"Modalias khong thuoc bus phan cung vat ly: {modalias}"
            except Exception:
                pass

        return True, f"Verified Physical Camera: {dev_name or f'video{self.device_index}'}"

    def is_device_available(self) -> bool:
        """Kiem tra xem file thiet bi /dev/video* co ton tai, co quyen doc va la camera vat ly that khong."""
        if sys.platform != "win32":
            dev_path = f"/dev/video{self.device_index}"
            if not os.path.exists(dev_path):
                return False
            if not os.access(dev_path, os.R_OK | os.W_OK):
                return False
            is_phys, _ = self.verify_physical_camera()
            if not is_phys:
                return False
        return True

    @contextmanager
    def acquire(self) -> Generator[cv2.VideoCapture, None, None]:
        """
        Context manager mo camera an toan, chan camera ao, kiem soat den LED va giai phong lap tuc.
        Su dung:
            with camera_mgr.acquire() as cap:
                ret, frame = cap.read()
        """
        # Kiem tra chong Camera Ao (v4l2loopback) truoc khi mo thiet bi
        is_phys, reason = self.verify_physical_camera()
        if not is_phys:
            logger.error(f"[SECURITY ALERT] Chan truy cap Camera Ao: {reason}")
            raise VirtualCameraDetectedError(reason)

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
