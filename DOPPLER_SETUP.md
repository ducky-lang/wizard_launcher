# Doppler Setup cho Wizard Launcher

Tài liệu này hướng dẫn cách cấu hình Wizard Launcher để lấy secrets từ Doppler thay vì Azure Key Vault.

## Cho Nhà Phát Triển (Tạo Build)

### Bước 1: Tạo Azure App Registration (không đổi)

Client ID dùng cho đăng nhập Microsoft (device code flow) không liên quan tới Doppler - vẫn làm như cũ:

```bash
az login
az ad app create --display-name "Wizard Launcher" --public-client-redirect-uris http://localhost
az ad app list --display-name "Wizard Launcher"
```

Sao chép `appId` - đây là PUBLIC CLIENT ID (không phải secret). Build vẫn dùng
`python tools/build.py --from-env` như trước.

### Bước 2: Tạo Doppler Project

```bash
# Cài Doppler CLI (nếu chưa có)
# Windows: scoop install doppler   |   macOS: brew install dopplerhq/cli/doppler

doppler login

# Tạo project và config
doppler projects create wizard-launcher
doppler configs create prd --project wizard-launcher
```

### Bước 3: Tạo Service Token

```bash
doppler configs tokens create launcher-runtime \
  --project wizard-launcher --config prd --plain
```

Sao chép token in ra - đây là secret, không commit vào git, không log lại.

### Bước 4: Build với Embedded Client ID

```bash
# Client ID vẫn embedded như trước - không liên quan tới Doppler
$env:MC_LAUNCHER_CLIENT_ID = "11111111-2222-3333-4444-555555555555"
python tools/build.py --from-env
```

## Cho Người Dùng Cuối (Cài Đặt & Dùng)

### Lần Đầu Chạy

1. Cài Wizard Launcher từ installer
2. Lần đầu chạy, app hiện code đăng nhập Microsoft (không đổi so với trước):
   ```
   Vào https://microsoft.com/devicelogin
   Nhập code: XXXXXX
   Chọn tài khoản của bạn
   ```
3. **Tự động done** - ứng dụng lưu token an toàn

### Sau Đó

- App dùng Doppler để lấy secrets (credentials, API keys, etc.) - nếu launcher
  được chạy qua `doppler run`, secrets đã có sẵn trong environment và không
  cần gọi mạng gì cả
- Toàn bộ communication tới API Doppler đã mã hóa (TLS)
- Secrets không bao giờ lưu ở ổ cứng dạng plaintext

## 🔐 Bảo Mật: Giải Thích Chi Tiết

### Tầng 1 & 2: Client ID + Device Code Flow (không đổi)

Xem `SECURITY_ARCHITECTURE.md` - phần này độc lập với việc lấy secrets ở đâu.

### Tầng 3: Doppler

```
Wizard Launcher
├─ Embedded Client ID (public, obfuscated)
│
└─ Đọc secrets, theo thứ tự ưu tiên:
   1. Biến môi trường (nếu chạy qua `doppler run -- WizardLauncher.exe`)
   2. Doppler API, dùng DOPPLER_TOKEN (nếu không chạy qua doppler run)

Doppler lưu:
  ✅ Mã hóa tại chỗ và khi truyền (TLS)
  ✅ Audit log (ai lấy gì khi nào)
  ✅ Role-based access + service token theo project/config
  ✅ Không bao giờ plaintext trên ổ cứng người dùng
```

**Tại sao ưu tiên environment trước API?** Đây là cách Doppler được thiết kế
để dùng: `doppler run` bơm thẳng secrets vào process, không cần token nào
sống trong máy người chơi. DOPPLER_TOKEN chỉ cần thiết cho các trường hợp
launcher không được khởi chạy qua `doppler run` (ví dụ máy dev, CI).

## Ví Dụ: Lưu & Lấy Database Password

**Lần đầu - người quản lý lưu (trên máy của họ):**
```bash
doppler secrets set DATABASE_PASSWORD "your-secure-password" \
  --project wizard-launcher --config prd
```

hoặc bằng Python:
```python
from launcher_core.doppler_secrets import initialize_doppler, get_doppler_manager
initialize_doppler(project="wizard-launcher", config="prd", token="dp.st...")
mgr = get_doppler_manager()
mgr.set_secret("DATABASE_PASSWORD", "your-secure-password")
```

**Mọi người dùng lấy:**
```python
from launcher_core.doppler_secrets import get_doppler_manager

mgr = get_doppler_manager()
db_password = mgr.get_secret("DATABASE_PASSWORD")
# Sử dụng để kết nối database
```

## Troubleshooting

**Lỗi: "Doppler not initialized"**
- Chưa gọi `initialize_doppler()` - kiểm tra `DOPPLER_PROJECT`/`DOPPLER_TOKEN`
  đã được set trước khi launcher khởi động

**Lỗi: "No DOPPLER_TOKEN set; cannot reach the Doppler API..."**
- Secret không có sẵn trong environment (không chạy qua `doppler run`) và
  cũng chưa set `DOPPLER_TOKEN`
- Chạy lại qua `doppler run -- WizardLauncher.exe`, hoặc set `DOPPLER_TOKEN`

**Lỗi: "Secret 'X' not found in Doppler"**
- Secret chưa được tạo trong config đó
- Tạo bằng: `doppler secrets set X "value" --project wizard-launcher --config prd`

## Biến Môi Trường

```powershell
$env:DOPPLER_PROJECT = "wizard-launcher"
$env:DOPPLER_CONFIG  = "prd"
$env:DOPPLER_TOKEN   = "dp.st.prd.xxxxxxxx"   # chỉ cần khi không chạy qua `doppler run`
```

Nếu muốn override cho dev:

```powershell
$env:DOPPLER_CONFIG = "dev"
```
