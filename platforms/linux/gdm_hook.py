"""
Dong bo hoa thong bao man hinh khoa GDM khi Bat / Tat ung dung FaceID:
- Khi FaceID BAT (ENABLED): kich hoat ban dich Tieng Viet cho GDM (thay the chu tieng Anh bang cau tieng Viet).
- Khi FaceID TAT (DISABLED/BYPASS): vo hieu hoa ban dich, tra lai 100% nguyen ban goc tieng Anh cua he thong.
"""
import os
import sys

GDM_MO_PATHS = [
    "/usr/share/locale-langpack/en/LC_MESSAGES/gdm.mo",
    "/usr/share/locale-langpack/en_US/LC_MESSAGES/gdm.mo",
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MO_SOURCE = os.path.join(PROJECT_ROOT, "data", "gdm_faceid.mo")


def sync_gdm_localization(enabled: bool):
    """Dong bo trang thai GDM localization theo cong tac FaceID."""
    if sys.platform == "win32":
        return

    try:
        if enabled:
            if os.path.exists(MO_SOURCE):
                with open(MO_SOURCE, "rb") as sf:
                    data = sf.read()
                for p in GDM_MO_PATHS:
                    try:
                        os.makedirs(os.path.dirname(p), exist_ok=True)
                        with open(p, "wb") as df:
                            df.write(data)
                    except Exception:
                        pass
        else:
            # Khi TAT ung dung: ghi rong file MO de GDM tra ve nguyen ban nhu cu
            for p in GDM_MO_PATHS:
                try:
                    if os.path.exists(p):
                        with open(p, "wb") as df:
                            pass
                except Exception:
                    pass
    except Exception:
        pass


def remove_gdm_localization():
    """Go bo hoan toan cac file MO khi go cai dat ung dung."""
    if sys.platform == "win32":
        return
    for p in GDM_MO_PATHS:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
