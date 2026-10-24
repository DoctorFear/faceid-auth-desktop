"""
Ho tro thiet bi camera tren moi truong Windows (DSHOW backend).
"""

import cv2
from contextlib import contextmanager
from typing import Generator, Optional


class WindowsCameraManager:
    def __init__(self, device_index: int = 0, width: int = 640, height: int = 480):
        self.device_index = device_index
        self.width = width
        self.height = height

    @contextmanager
    def acquire(self) -> Generator[cv2.VideoCapture, None, None]:
        cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(self.device_index)
        if not cap.isOpened():
            raise RuntimeError(f"Khong the mo camera Windows tai index {self.device_index}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        try:
            yield cap
        finally:
            cap.release()
