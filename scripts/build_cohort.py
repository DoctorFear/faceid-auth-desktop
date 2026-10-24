import argparse
import os
import sys
import time
import urllib.request
import urllib.parse
import json
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.embedder import ArcFaceEmbedder

DEFAULT_OUTPUT = os.path.join(BASE_DIR, "data", "cohort_lfw.npy")
RAW_FACES_DIR = os.path.join(BASE_DIR, "data", "datasets", "cohort_raw")


def extract_cohort_from_directory(
    images_dir: str,
    output_path: str = DEFAULT_OUTPUT,
    max_cohort: int = 150
) -> int:
    """Doc thu muc chua anh khuon mat, trich xuat embedding ArcFace va luu file npy."""
    if not os.path.exists(images_dir):
        print(f"[-] Thu muc khong ton tai: {images_dir}")
        return 0

    valid_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    identity_to_images = {}
    for root, _, files in os.walk(images_dir):
        folder_name = os.path.basename(root)
        valid_files = [os.path.join(root, f) for f in files if os.path.splitext(f)[1].lower() in valid_exts]
        if valid_files:
            identity_to_images[root] = sorted(valid_files)

    if not identity_to_images:
        print(f"[-] Khong tim thay anh hop le nao trong: {images_dir}")
        return 0

    if len(identity_to_images) == 1:
        # Thu muc phang: tat ca anh nam cung 1 thu muc
        image_paths = list(identity_to_images.values())[0]
    else:
        # Cau truc LFW (moi nguoi 1 folder con): lay 1 anh dai dien cho moi nguoi de dam bao da dang
        image_paths = [imgs[0] for imgs in identity_to_images.values()]

    print(f"[*] Tim thay {len(image_paths)} doi tuong khac nhau. Bat dau trich xuat (toi da {max_cohort})...")
    detector = YuNetDetector()
    embedder = ArcFaceEmbedder()

    embeddings = []
    count = 0

    for path in image_paths:
        if count >= max_cohort:
            break
        try:
            img = cv2.imread(path)
            if img is None or img.size == 0:
                continue

            detections = detector.detect(img)
            if len(detections) != 1:
                # Chi lay nhung anh co dung 1 khuon mat ro rang
                continue

            box, landmarks, score = detections[0]
            emb = embedder.extract(img, box, landmarks=landmarks)
            norm = np.linalg.norm(emb) + 1e-9
            normalized_emb = emb / norm
            embeddings.append(normalized_emb)
            count += 1
            print(f"    [{count:03d}/{max_cohort}] OK: {os.path.basename(path)}")
        except Exception as e:
            continue

    if count == 0:
        print("[-] Khong trich xuat duoc vector nao hop le.")
        return 0

    arr = np.array(embeddings, dtype=np.float32)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    np.save(output_path, arr)
    print(f"\n[+] THANH CONG: Da luu {count} vector cohort vao: {output_path}")
    print(f"    Shape: {arr.shape} | Dtype: {arr.dtype} | File size: {os.path.getsize(output_path):,} bytes")
    return count


def download_public_sample_faces(save_dir: str = RAW_FACES_DIR, count: int = 120) -> int:
    """Tu dong tai cac anh chan dung nhan vat cong khai tu Wikimedia Commons de lam cohort."""
    os.makedirs(save_dir, exist_ok=True)
    print(f"[*] Dang tai danh sach anh chan dung tu Wikimedia Commons...")

    # Tim kiem danh sach chan dung nhan vat lich su / nghe si co san tren Wikimedia Commons
    search_queries = [
        "Portrait_of_a_man", "Portrait_of_a_woman", "Headshot", 
        "President_portrait", "Official_portrait_2020", "Official_portrait_2021",
        "Official_portrait_2022", "Official_portrait_2023", "Cabinet_official_portrait"
    ]

    downloaded = 0
    seen_urls = set()

    for q in search_queries:
        if downloaded >= count:
            break
        api_url = (
            f"https://commons.wikimedia.org/w/api.php?action=query&generator=search"
            f"&gsrsearch={urllib.parse.quote(q)}&gsrnamespace=6&gsrlimit=35"
            f"&prop=imageinfo&iiprop=url|mime&iiurlwidth=400&format=json"
        )
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "FaceIDCohortBuilder/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            for pid, pinfo in pages.items():
                if downloaded >= count:
                    break
                img_info = pinfo.get("imageinfo", [{}])[0]
                mime = img_info.get("mime", "")
                url = img_info.get("thumburl") or img_info.get("url", "")
                if ("image/jpeg" in mime or "image/png" in mime) and url and url not in seen_urls:
                    seen_urls.add(url)
                    ext = ".jpg" if "jpeg" in mime else ".png"
                    target_file = os.path.join(save_dir, f"cohort_{downloaded+1:03d}{ext}")
                    try:
                        dl_req = urllib.request.Request(url, headers={"User-Agent": "FaceIDCohortBuilder/1.0"})
                        with urllib.request.urlopen(dl_req, timeout=10) as dl_resp, open(target_file, "wb") as f:
                            f.write(dl_resp.read())
                        downloaded += 1
                        print(f"    Da tai [{downloaded:03d}/{count}]: {os.path.basename(target_file)} ({os.path.getsize(target_file)/1024:.1f} KB)")
                        time.sleep(0.05)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[-] Loi khi truy van query '{q}': {e}")
            continue

    print(f"[*] Tong so anh da tai: {downloaded} vao {save_dir}")
    return downloaded


def download_and_extract_lfw(dataset_dir: str = os.path.join(BASE_DIR, "data", "datasets", "lfw")) -> str:
    """Tai truc tiep file zip LFW chuan tu Hugging Face mirror va giai nen."""
    os.makedirs(os.path.dirname(dataset_dir), exist_ok=True)
    zip_path = os.path.join(os.path.dirname(dataset_dir), "lfw_multifaces-ingestion.zip")
    url = "https://huggingface.co/datasets/vilsonrodrigues/lfw/resolve/main/lfw_multifaces-ingestion.zip"

    if not os.path.exists(zip_path) or os.path.getsize(zip_path) < 60 * 1024 * 1024:
        print(f"[*] Dang tai LFW Dataset tu Hugging Face mirror (~66 MB)...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as f:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            last_print = time.time()
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if time.time() - last_print > 0.5 or downloaded == total_size:
                    percent = (downloaded / total_size * 100) if total_size else 0
                    print(f"    Tien do tai: {downloaded / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB ({percent:.1f}%)", end="\r")
                    last_print = time.time()
            print()
        print(f"[+] Da tai xong: {zip_path}")

    import zipfile
    print(f"[*] Dang giai nen LFW vao: {dataset_dir} ...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(dataset_dir)
    print(f"[+] Da giai nen thanh cong LFW dataset!")
    return dataset_dir


def main():
    parser = argparse.ArgumentParser(description="Tao tap Cohort Embeddings (data/cohort_lfw.npy) cho S-Norm")
    parser.add_argument("--images-dir", type=str, default="", help="Duong dan thu muc chua anh mat (LFW, CelebA...)")
    parser.add_argument("--download-lfw", action="store_true", help="Tu dong tai LFW dataset chuan (~66MB) va giai nen")
    parser.add_argument("--download-auto", action="store_true", help="Tu dong tai anh chan dung Wikimedia ve tao cohort")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="File .npy dau ra")
    parser.add_argument("--max-faces", type=int, default=128, help="So luong face embeddings can trich xuat (100-200)")
    args = parser.parse_args()

    input_dir = args.images_dir.strip()

    if args.download_lfw:
        print("[*] Che do --download-lfw: Dang tu dong tai va giai nen tap LFW...")
        input_dir = download_and_extract_lfw()

    elif args.download_auto:
        print("[*] Che do --download-auto: Tu dong tai dataset chan dung cong khai...")
        download_public_sample_faces(save_dir=RAW_FACES_DIR, count=args.max_faces)
        input_dir = RAW_FACES_DIR

    if not input_dir:
        # Neu thu muc data/datasets/lfw hoac cohort_raw da co anh
        lfw_dir = os.path.join(BASE_DIR, "data", "datasets", "lfw")
        if os.path.exists(lfw_dir) and len(os.listdir(lfw_dir)) > 0:
            input_dir = lfw_dir
        elif os.path.exists(RAW_FACES_DIR) and len(os.listdir(RAW_FACES_DIR)) > 0:
            input_dir = RAW_FACES_DIR
        else:
            print("=====================================================================")
            print("HUONG DAN TAO FILE data/cohort_lfw.npy CHO S-NORM:")
            print("1. Cach 1 (Tu dong tai LFW chuan):")
            print("   python scripts/build_cohort.py --download-lfw")
            print("\n2. Cach 2 (Dung thu muc anh LFW/CelebA co san tren may):")
            print("   python scripts/build_cohort.py --images-dir C:\\duong\\dan\\anh")
            print("=====================================================================")
            return

    extract_cohort_from_directory(input_dir, output_path=args.output, max_cohort=args.max_faces)


if __name__ == "__main__":
    main()
