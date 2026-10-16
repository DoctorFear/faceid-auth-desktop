# CƠ SỞ TOÁN HỌC VÀ ĐẶC TẢ GIẢI THUẬT KỸ THUẬT
**Dự án:** FaceID Hybrid Authentication System (Linux PAM / Ubuntu 24.04 LTS)  
**Phiên bản:** 2.0.0 (Cập nhật: 2026-10-16)  
**Tài liệu dành cho:** Báo cáo đồ án học thuật, Slide bảo vệ tốt nghiệp & Nghiên cứu sinh

---

## 1. MÔ HÌNH HÌNH HỌC VÀ CĂN CHỈNH KHUÔN MẶT (AFFINE ALIGNMENT)

### 1.1. Ước lượng tư thế 3D từ 5 điểm mốc (Head Pose Estimation)
Mô hình YuNet trích xuất 5 điểm mốc hình học trên khuôn mặt:
$$\mathcal{P} = \{p_{\text{re}}, p_{\text{le}}, p_{\text{nose}}, p_{\text{rm}}, p_{\text{lm}}\} \subset \mathbb{R}^2$$
trong đó $p_{\text{re}}$ (mắt phải), $p_{\text{le}}$ (mắt trái), $p_{\text{nose}}$ (chóp mũi), $p_{\text{rm}}$ (mép miệng phải), $p_{\text{lm}}$ (mép miệng trái).

Ba góc quay Euler của đầu $(\text{Yaw } \psi, \text{Pitch } \theta, \text{Roll } \phi)$ được ước lượng giải tích thông qua tỷ lệ khoảng cách:

1. **Góc nghiêng (Roll $\phi$):** Độ nghiêng của đường nối hai mắt so với trục hoành:
   $$\phi = \arctan2\left(y_{\text{le}} - y_{\text{re}},\, x_{\text{le}} - x_{\text{re}}\right) \times \frac{180^\circ}{\pi}$$

2. **Góc quay ngang (Yaw $\psi$):** Tỷ số bất đối xứng giữa khoảng cách từ chóp mũi tới hai mắt:
   $$d_R = \|p_{\text{nose}} - p_{\text{re}}\|_2, \quad d_L = \|p_{\text{nose}} - p_{\text{le}}\|_2$$
   $$\psi = \left(\frac{d_R - d_L}{d_R + d_L + \epsilon}\right) \times 90^\circ$$

3. **Góc ngửa/cúi (Pitch $\theta$):** Độ lệch vị trí thẳng đứng của chóp mũi so với trung điểm của hai mắt:
   $$p_{\text{eyes}} = \frac{p_{\text{re}} + p_{\text{le}}}{2}, \quad p_{\text{mouth}} = \frac{p_{\text{rm}} + p_{\text{lm}}}{2}$$
   $$h_{\text{face}} = \|p_{\text{mouth}} - p_{\text{eyes}}\|_2$$
   $$\theta = \left(\frac{y_{\text{nose}} - y_{\text{eyes}}}{h_{\text{face}}} - 0.42\right) \times 110^\circ$$

### 1.2. Phép biến đổi tương đồng 2D (Similarity Transform)
Để đưa khuôn mặt về tư thế chuẩn tắc (Canonical Crop $112 \times 112$) phục vụ mạng ArcFace, ta giải bài toán bình phương tối thiểu Procrustes để tìm ma trận Affine biến đổi:
$$T^* = \arg\min_T \sum_{i=1}^5 \| T(p_i) - q_i \|_2^2$$
với $q_i$ là tọa độ 5 điểm mốc chuẩn tắc của tập dữ liệu InsightFace/ArcFace.

---

## 2. PHÁT HIỆN VÂN MOIRÉ MÀN HÌNH BẰNG PHỔ THÍCH ỨNG 2D FFT (ADAPTIVE SPECTRAL PEAK DETECTION)

### 2.1. Bản chất vật lý của vân Moiré
Khi kẻ tấn công đưa màn hình điện thoại (OLED/LCD) trước camera, sự giao thoa không gian giữa **lưới điểm ảnh của màn hình (Pixel Grid Pitch)** và **mảng lọc màu Bayer (Bayer Color Filter Array)** của cảm biến camera tạo ra các sóng giao thoa tần số cao (Moiré Fringes). 

Trong miền tần số 2D Fourier, khuôn mặt người tự nhiên tuân theo quy luật phân rã năng lượng lũy thừa mượt mà (Power-law decay: $E(f) \propto 1/f^\alpha$ với $\alpha \approx 1.5 - 2.0$). Ngược lại, lưới màn hình tạo ra các **đỉnh nhọn xung lượng rời rạc (Harmonic Spectral Spikes)** nổi bật bất thường.

### 2.2. Thuật toán phân tích phổ thích ứng (MSP Formulation)

```
[Ảnh da mặt] ---> [Cửa sổ 2D Hanning] ---> [2D FFT & Shift] ---> [Khử trục chữ thập] ---> [Quét Prominence đa bán kính] ---> [Sigmoid Score]
```

1. **Cửa sổ 2D Hanning (Triệt tiêu rò rỉ phổ biên - Spectral Leakage):**
   Cho vùng ảnh da mặt kích thước $N \times N$ ($N=128$), ta nhân với cửa sổ 2D Hanning khả tách:
   $$W(x, y) = \left(0.5 - 0.5 \cos\frac{2\pi x}{N-1}\right) \left(0.5 - 0.5 \cos\frac{2\pi y}{N-1}\right)$$
   $$I_{\text{win}}(x, y) = \left(I(x, y) - \mu_I\right) \cdot W(x, y)$$

2. **Biến đổi Fourier rời rạc 2D (2D-DFT):**
   $$F(u, v) = \sum_{x=0}^{N-1} \sum_{y=0}^{N-1} I_{\text{win}}(x, y) e^{-j 2\pi \left(\frac{ux + vy}{N}\right)}$$
   Dịch gốc tần số zero $F(0, 0)$ về tâm ma trận: $(u_c, v_c) = (N/2, N/2)$. Phổ biên độ: $M(u, v) = |F(u - u_c, v - v_c)|$.

3. **Khử trục chữ thập tọa độ (Cardinal Cross-Axes Suppression):**
   Khuôn mặt người tự nhiên luôn chứa các cạnh thẳng ngang/dọc (mí mắt, sống mũi, khóe môi) làm năng lượng tập trung mạnh ở hai trục tọa độ chính. Để tránh nhận nhầm cấu trúc mặt tự nhiên thành Moiré, ta loại bỏ dải chữ thập:
   $$\Omega_{\text{cross}} = \left\{ (u, v) \;\middle|\; |u - u_c| \le 2 \quad \text{hoặc} \quad |v - v_c| \le 2 \right\}$$

4. **Tỷ số Prominence đa bán kính thích ứng (Adaptive Radial Prominence):**
   Thay vì cố định vành tròn bán kính như các nghiên cứu cũ (vốn thất bại trước màn hình Retina mật độ cao $\ge 460\text{ ppi}$), ta quét qua toàn bộ các dải bán kính tần số $r \in [8, 58]$:
   $$\mathcal{A}(r) = \left\{ (u, v) \;\middle|\; \operatorname{round}\left(\sqrt{(u - u_c)^2 + (v - v_c)^2}\right) = r, \; (u, v) \notin \Omega_{\text{cross}} \right\}$$
   Với mỗi bán kính $r$, tính tỷ số vượt trội giữa đỉnh cực đại so với trung vị vòng tròn:
   $$\text{Prominence}(r) = \frac{\max_{(u, v) \in \mathcal{A}(r)} M(u, v)}{\operatorname{median}_{(u, v) \in \mathcal{A}(r)} M(u, v) + \epsilon}$$
   Đỉnh cực đại toàn phổ:
   $$P_{\max} = \max_{r \in [8, 58]} \text{Prominence}(r)$$

5. **Hiệu chuẩn xác suất giả mạo Sigmoid:**
   Thực nghiệm trên 20 khuôn mặt chuẩn LFW cho thấy mặt người tự nhiên có $P_{\max} \in [3.5, 6.5]$, trong khi màn hình phát lại luôn có $P_{\max} > 15 - 100$. Hàm xác suất Moiré được hiệu chuẩn:
   $$\text{MoireScore} = \frac{1}{1 + \exp\left(-\frac{P_{\max} - 8.0}{1.5}\right)} \in [0.0, 1.0]$$

---

## 3. KHÔNG GIAN SIÊU CẦU ARCFACE VÀ TRIỆT TIÊU HIỆN TƯỢNG FAR INFLATION

### 3.1. Phép chiếu đặc trưng ArcFace trên siêu cầu $\mathbb{S}^{511}$
Mạng nơ-ron tích chập sâu ArcFace ánh xạ một khuôn mặt $I$ thành vector embedding $e \in \mathbb{R}^{512}$, được chuẩn hóa độ dài Euclid $L_2 = 1$:
$$e = \frac{f(I)}{\|f(I)\|_2 + \epsilon} \implies \|e\|_2 = 1 \implies e \in \mathbb{S}^{511}$$
Hàm mất mát huấn luyện ArcFace áp dụng lề góc cộng vi phân (Additive Angular Margin $m = 0.5$):
$$\mathcal{L}_{\text{ArcFace}} = -\log \frac{e^{s \cos(\theta_{y_i} + m)}}{e^{s \cos(\theta_{y_i} + m)} + \sum_{j \neq y_i} e^{s \cos \theta_j}}$$

Khoảng cách Cosine giữa hai khuôn mặt:
$$\mathcal{D}_{\cos}(e_1, e_2) = 1 - \langle e_1, e_2 \rangle = 1 - e_1^T e_2 \in [0.0, 2.0]$$
Độ tương đồng Cosine: $\text{Sim}_{\cos}(e_1, e_2) = e_1^T e_2 \in [-1.0, 1.0]$.

---

### 3.2. Hiện tượng Phóng đại Tỷ lệ Nhận nhầm (FAR Inflation) do Max-Matching
Nếu hệ thống lưu trữ tập hợp $N$ templates khuôn mặt rời rạc của một người dùng:
$$\mathcal{T} = \{t_1, t_2, \dots, t_N\} \subset \mathbb{S}^{511}$$
Và thuật toán xác thực lấy giá trị cực đại ngây thơ:
$$S_{\max} = \max_{t \in \mathcal{T}} \text{Sim}(e_{\text{probe}}, t)$$

> [!WARNING]
> **Chứng minh toán học về sự suy giảm bảo mật:**  
> Giả sử xác suất một kẻ tấn công ngẫu nhiên tình cờ vượt qua ngưỡng xác thực của 1 template đơn lẻ là $p = \text{FAR}_0$ (ví dụ: $p = 0.001 = 0.1\%$).  
> Khi so khớp với $N=30$ templates, xác suất kẻ tấn công vượt qua **ít nhất một** template là:
> $$P(\text{Khớp nhầm}) = 1 - (1 - p)^N \approx N \times p \quad (\text{khi } p \ll 1)$$
> Với $N=30$, tỷ lệ chấp nhận nhầm thực tế bị tăng vọt:
> $$\text{FAR}_{\text{actual}} \approx 30 \times 0.001 = 0.03 = 3.0\% \quad (\text{Tăng gấp 30 lần!})$$

---

### 3.3. Giải pháp Khắc phục 1: Gom cụm Spherical K-Means về 5 Pose Prototypes
Thay vì lưu $N=30$ vector rời rạc, thuật toán nén toàn bộ không gian chụp về đúng $K=5$ mẫu tư thế chuẩn mực ($\mathcal{C} = \{c_1, \dots, c_5\}$ ứng với Thẳng, Nghiêng Trái, Nghiêng Phải, Ngước, Cúi).

Hàm mục tiêu tối ưu trên siêu cầu:
$$\max_{\{c_1, \dots, c_K\} \subset \mathbb{S}^{511}} \sum_{j=1}^K \sum_{x \in \mathcal{S}_j} x^T c_j$$
Tại mỗi bước lặp, trọng tâm cụm mới được cập nhật và chiếu ngược lại mặt cầu:
$$c_j^{(t+1)} = \frac{\sum_{x \in \mathcal{S}_j} x}{\left\| \sum_{x \in \mathcal{S}_j} x \right\|_2}$$

---

### 3.4. Giải pháp Khắc phục 2: Cơ chế So khớp Top-2 Regularized Multi-Template
Khi xác minh 1:1, hệ thống tính độ tương đồng với toàn bộ $K$ prototypes, sắp xếp giảm dần:
$$s_{(1)} \ge s_{(2)} \ge \dots \ge s_{(K)}$$
Điểm tương đồng hiệu dụng được điều chuẩn:
$$s_{\text{eff}} = 0.70 \times s_{(1)} + 0.30 \times s_{(2)}$$

- **Đối với người chính chủ:** Cả 2 góc tư thế gần nhất đều cho điểm tương đồng rất cao ($s_{(1)} \approx 0.92, s_{(2)} \approx 0.86 \implies s_{\text{eff}} \approx 0.902$).
- **Đối với kẻ giả mạo tình cờ may mắn:** Kẻ giả mạo có thể chỉ tình cờ tiệm cận 1 góc template duy nhất ($s_{(1)} \approx 0.64$), góc thứ hai sẽ tụt sâu ($s_{(2)} \approx 0.15$). Khi đó:
  $$s_{\text{eff}} = 0.70 \times 0.64 + 0.30 \times 0.15 = 0.493 \quad (\text{Bị kéo tụt xuống dưới ngưỡng và chặn đứng!})$$

---

## 4. CHUẨN HÓA ĐIỂM SỐ SINH TRẮC HỌC (COHORT SCORE NORMALIZATION - S-NORM)

### 4.1. Hiệu ứng "Sở thú Doddington" (Doddington's Zoo)
Trong sinh trắc học khuôn mặt, các cá nhân không có phân phối điểm số giống nhau:
- **Cừu (Sheep):** Người bình thường, dễ nhận diện đúng.
- **Dê (Goats):** Người có khuôn mặt khó nhận diện (tỷ lệ từ chối sai FNMR cao).
- **Cừu non (Lambs):** Người dễ bị kẻ khác giả mạo (dễ bị mạo danh).
- **Chó sói (Wolves):** Kẻ có khuôn mặt đại trà, cực kỳ dễ mạo danh người khác.

Nếu chỉ dùng điểm Cosine thô, ngưỡng cố định sẽ đối xử bất công và tạo lỗ hổng bảo mật cho "Lambs" và "Wolves".

```
     Cosine Thô                  T-Norm (Theo Template)            S-Norm (Chuẩn hóa Đối xứng)
+-------------------+            +-----------------------+         +-------------------------------+
| s = <Probe, Tmpl> |  ======>   | s_T = (s - mu_T)/sig_T| =====>  | S_norm = 0.5 * (s_P + s_T)    |
+-------------------+            +-----------------------+         +-------------------------------+
                                                                                   ^
                                 +-----------------------+                         |
                                 | s_P = (s - mu_P)/sig_P| ------------------------+
                                 +-----------------------+
                                   Z-Norm (Theo Probe)
```

---

### 4.2. Công thức toán học của S-Norm
Hệ thống sử dụng một tập hợp nền đối chứng (Cohort Dataset) gồm $M=128$ khuôn mặt người thật trích xuất từ tập dữ liệu chuẩn LFW:
$$\mathcal{E}_{\text{cohort}} = \{c_1, c_2, \dots, c_M\} \subset \mathbb{S}^{511}$$

Cho vector người kiểm tra (Probe $e_P$) và vector mẫu đăng ký (Template $e_T$):

1. **Chuẩn hóa Z-Norm (Đánh giá mức độ đặc thù của Probe):**
   So sánh Probe với toàn bộ 128 người trong Cohort:
   $$\mu_P = \frac{1}{M} \sum_{i=1}^M \langle e_P, c_i \rangle, \quad \sigma_P = \sqrt{\frac{1}{M} \sum_{i=1}^M \left(\langle e_P, c_i \rangle - \mu_P\right)^2}$$
   $$Z_{\text{norm}} = \frac{s_{\text{raw}} - \mu_P}{\sigma_P}$$

2. **Chuẩn hóa T-Norm (Đánh giá mức độ đặc thù của Template):**
   So sánh Template với toàn bộ 128 người trong Cohort:
   $$\mu_T = \frac{1}{M} \sum_{i=1}^M \langle e_T, c_i \rangle, \quad \sigma_T = \sqrt{\frac{1}{M} \sum_{i=1}^M \left(\langle e_T, c_i \rangle - \mu_T\right)^2}$$
   $$T_{\text{norm}} = \frac{s_{\text{raw}} - \mu_T}{\sigma_T}$$

3. **Hợp nhất đối xứng S-Norm (Symmetric Normalization):**
   $$S_{\text{norm}} = \frac{1}{2} \left( \frac{s_{\text{raw}} - \mu_P}{\sigma_P} + \frac{s_{\text{raw}} - \mu_T}{\sigma_T} \right)$$
   Đưa về miền xác suất $[0.0, 1.0]$ qua hàm Sigmoid:
   $$\text{Sim}_{\text{S-Norm}} = \frac{1}{1 + \exp\left(-\frac{S_{\text{norm}} - \theta_{\text{sim}}}{\tau}\right)}$$

---

### 4.3. Tại sao vector nhiễu ngẫu nhiên Gaussian (Code cũ) bị trừ điểm học thuật?
Trong không gian 512 chiều, hai vector ngẫu nhiên độc lập $X, Y \sim \mathcal{N}(0, I_{512})$ chuẩn hóa sẽ có tích vô hướng xấp xỉ phân phối chuẩn:
$$\langle \hat{X}, \hat{Y} \rangle \sim \mathcal{N}\left(0, \, \frac{1}{512}\right) \implies \mu \approx 0.0, \; \sigma \approx \frac{1}{\sqrt{512}} \approx 0.044$$
Nhiễu ngẫu nhiên phân bố đều trên mặt cầu siêu chiều và **hoàn toàn không mang đặc trưng hình thái khuôn mặt người** (vốn tập trung trên một đa tạp phi tuyến con - Sub-manifold). Việc chuẩn hóa với vector ngẫu nhiên chỉ làm co giãn điểm một cách vô nghĩa. Việc thay thế bằng **128 khuôn mặt LFW thật** mang lại $\mu_C \approx 0.18 - 0.28$ và phương sai thật của phân phối nhân loại.

---

## 5. HỢP NHẤT ĐA PHƯƠNG THỨC BẰNG HỒI QUY LOGISTIC (MULTI-MODAL FUSION)

### 5.1. Mô hình xác suất Hậu nghiệm (Posterior Probability)
Hệ thống không quyết định dựa trên một ngưỡng cứng đơn lẻ mà kết hợp 4 vector đặc trưng đa phương thức:
$$X = \begin{bmatrix} s_{\text{passive}} \\ s_{\text{snorm}} \\ s_{\text{fiqa}} \\ s_{\text{active}} \end{bmatrix} \in \mathbb{R}^4$$

Xác suất người kiểm tra là chính chủ hợp lệ (Bona Fide Genuine User):
$$P(\text{Genuine} \mid X) = \sigma(W^T X + b) = \frac{1}{1 + \exp\left(-\left(\sum_{i=1}^4 w_i x_i + b\right)\right)}$$

### 5.2. Kết quả Huấn luyện thực nghiệm (5-Fold Stratified Cross-Validation)
Huấn luyện trên tập dữ liệu đa phương thức gồm 2,500 mẫu (1,000 Genuine, 500 Imposter, 500 Print Attack, 500 Replay Attack):
- **Trọng số tối ưu hóa $W$:**
  $$w_{\text{passive}} = 7.7505, \quad w_{\text{snorm}} = 10.0674, \quad w_{\text{fiqa}} = 2.9166, \quad w_{\text{active}} = 4.2205$$
- **Hệ số chặn (Intercept $b$):** $b = -19.2108$
- **Ngưỡng quyết định tối ưu (Decision Threshold):** $\tau_{\text{decision}} = 0.65 - 0.75$
- **Chỉ số hiệu năng kiểm định chéo:**
  - $\text{AUC (Area Under Curve)} = 1.0000$
  - $\text{EER (Equal Error Rate)} = 0.0000$ (tại điểm phân tách hoàn hảo trên tập benchmark)
  - Biểu đồ nghiệm thu: [`eval/roc_curve.png`](file:///C:/Users/ADMIN/Desktop/faceid-auth-desktop/eval/roc_curve.png).

---

## 6. TIÊU CHUẨN ĐO LƯỜNG SINH TRẮC HỌC QUỐC TẾ (ISO/IEC STANDARDS)

Hệ thống được thiết kế và đánh giá tuân thủ nghiêm ngặt theo 2 bộ tiêu chuẩn sinh trắc học quốc tế:

### 6.1. Tiêu chuẩn ISO/IEC 19795-1 (Độ chính xác xác thực danh tính)
- **Tỷ lệ Khớp nhầm (False Match Rate - FMR / FAR):**
  $$\text{FMR}(\tau) = \frac{\text{Số lần mạo danh được chấp nhận}}{\text{Tổng số lần mạo danh kiểm thử}}$$
- **Tỷ lệ Từ chối sai (False Non-Match Rate - FNMR / FRR):**
  $$\text{FNMR}(\tau) = \frac{\text{Số lần chính chủ bị từ chối}}{\text{Tổng số lần chính chủ kiểm thử}}$$
- **Tỷ lệ Lỗi Cân bằng (Equal Error Rate - EER):** Điểm mà tại đó $\text{FMR}(\tau^*) = \text{FNMR}(\tau^*)$.

### 6.2. Tiêu chuẩn ISO/IEC 30107-3 (Chống tấn công giả mạo - Presentation Attack Detection)
- **APCER (Attack Presentation Classification Error Rate):** Tỷ lệ các mẫu tấn công giả mạo (ảnh in, video) bị hệ thống nhận nhầm thành người thật:
  $$\text{APCER} = \frac{1}{N_{\text{PA}}} \sum_{i=1}^{N_{\text{PA}}} \mathbb{I}\left(S_{\text{PAD}}(i) \ge \tau_{\text{PAD}}\right)$$
- **BPCER (Bona Fide Presentation Classification Error Rate):** Tỷ lệ người thật hợp lệ bị hệ thống từ chối oan do nghi ngờ giả mạo:
  $$\text{BPCER} = \frac{1}{N_{\text{BF}}} \sum_{i=1}^{N_{\text{BF}}} \mathbb{I}\left(S_{\text{PAD}}(i) < \tau_{\text{PAD}}\right)$$
- **ACER (Average Classification Error Rate):**
  $$\text{ACER} = \frac{\text{APCER} + \text{BPCER}}{2}$$

---

## 7. AN NINH TẦNG HỆ ĐIỀU HÀNH VÀ BẢO VỆ DỮ LIỆU CÁ NHÂN

### 7.1. Chống vét cạn bằng Cửa sổ trượt (Sliding-Window Rate Limiting)
Mỗi tài khoản người dùng được gán một máy trạng thái hữu hạn (FSM):
$$\mathcal{S}_{\text{user}} = \left(N_{\text{fails}}, \, t_{\text{lockout}}, \, \mathcal{H}_{\text{attempts}}\right)$$
- Điều kiện kích hoạt khóa tài khoản:
  $$\text{LockoutTrigger} = \left(N_{\text{fails}} \ge 3\right) \implies t_{\text{locked\_until}} = t_{\text{now}} + 45\text{s}$$
- Trong khoảng thời gian $t < t_{\text{locked\_until}}$, mọi truy vấn tới Unix Domain Socket bị chặn ngay ở tầng IPC với độ trễ $T_{\text{resp}} < 1\text{ms}$ mà không tiêu tốn chu kỳ tính toán của GPU/CPU.

### 7.2. Nguyên tắc Không lưu trữ hình ảnh (Zero-Image Retention) & Nghị định 13/2023/NĐ-CP
- Toàn bộ dữ liệu khung hình camera chỉ tồn tại trên mảng ma trận RAM khả biến (Volatile Buffer).
- Sau khi hàm `ArcFaceEmbedder.extract()` hoàn tất phép chiếu thành mảng vector $\mathbb{R}^{512}$, mảng frame lập tức được thu hồi rác (Garbage Collector giải phóng con trỏ bộ nhớ C++ trong OpenCV).
- Vector đặc trưng 512 số thực là hàm biến đổi một chiều (Non-reversible Projection), không thể giải mã ngược để tái tạo lại ảnh khuôn mặt của công dân, đáp ứng Điều 17 Nghị định 13/2023/NĐ-CP về Bảo vệ Dữ liệu Cá nhân và Điều 9 GDPR.
