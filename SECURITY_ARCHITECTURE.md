# Wizard Launcher - Security Architecture

Kế hoạch bảo mật toàn diện cho phân phối ứng dụng với Doppler.

## 🎯 Mục Tiêu Bảo Mật

| Mục Tiêu | Giải Pháp | Mục Đích |
|---------|----------|---------|
| Credentials không lộ trong source code | Embedded + Obfuscated | Ngăn lộ qua git history |
| Credentials không lộ trong binary | Obfuscation + Base64 | Ngăn lộ qua `strings` tool |
| Credentials không lộ trên ổ cứng | DPAPI + Keyring | Ngăn lộ khi ổ cứng bị đánh cắp |
| Credentials không lộ trên network | TLS + Azure SDK | Ngăn lộ khi sniff traffic |
| Người dùng không cần config | Device Code + Auto | Reduce friction, prevent mistakes |

## 📊 Ba Tầng Bảo Mật

### Tầng 1: Azure Client ID (Công Khai, Obfuscated)
```
┌─────────────────────────────────────┐
│  Source Code                        │
│  EMBEDDED_CLIENT_ID = ""  ← empty   │
└─────────────────────────────────────┘
                  ↓ python tools/embed_secret.py --from-env
┌─────────────────────────────────────┐
│  launcher_core/secure_store.py      │
│  EMBEDDED_CLIENT_ID = "jk83...=="   │ ← obfuscated base64
└─────────────────────────────────────┘
                  ↓ pyinstaller compile
┌─────────────────────────────────────┐
│  WizardLauncher.exe                 │
│  + crypto.deobfuscate(embedded_id)  │ ← runtime decode
└─────────────────────────────────────┘
                  ↓ python tools/embed_secret.py --clean
┌─────────────────────────────────────┐
│  Source Code                        │
│  EMBEDDED_CLIENT_ID = ""  ← cleaned │
└─────────────────────────────────────┘
```

**Tại sao?** Azure Client ID (public client) không phải secret, nhưng:
- Không nên hardcode ở dạng plaintext
- `strings WizardLauncher.exe | grep 1111...` sẽ không tìm thấy
- Casual reverse-engineer thấy base64, không phải UUID
- **Kết quả:** Ngăn quota abuse, không ngăn account compromise (không cần)

### Tầng 2: Microsoft Tokens (Người Dùng, Auto-Managed)
```
Lần 1: User chạy ứng dụng
       ↓
Device Code Flow:
  "Vào https://microsoft.com/devicelogin, nhập: XXXXXX"
       ↓
User đăng nhập → Microsoft → App nhận token
       ↓
Token lưu trong:
  • Windows Credential Manager (DPAPI encrypted)
  • Fallback: launcher_core/secure_store.py (DPAPI encrypted)

Lần 2+: Ứng dụng tự động dùng token cũ (user không thấy gì)
```

**Tại sao?** 
- User chỉ cần đăng nhập 1 lần
- Tokens tự động refresh
- Không cần password storage
- Không cần browser/manual login mỗi lần

### Tầng 3: Application Secrets (Doppler Managed)
```
Wizard Launcher
├─ Embedded Client ID (public, obfuscated)
│
└─ Đọc secrets (launcher_core/doppler_secrets.py)
   ├─ 1. Biến môi trường trước
   │     (có sẵn nếu chạy qua `doppler run -- WizardLauncher.exe`)
   └─ 2. Doppler API, dùng DOPPLER_TOKEN nếu (1) không có
         ├─ Database passwords
         ├─ API keys
         ├─ Server tokens
         ├─ ...

Doppler lưu:
  ✅ Mã hóa tại chỗ và khi truyền (TLS)
  ✅ Audit log (ai lấy gì khi nào)
  ✅ Role-based access + service token theo project/config
  ✅ Không bao giờ plaintext trên ổ cứng người dùng
```

Khác với Key Vault, bước xác thực Microsoft (Tầng 2) không còn là điều kiện
để đọc secrets - Doppler dùng service token riêng, độc lập với tài khoản
Microsoft của người chơi. Client ID + device code flow ở Tầng 1/2 vẫn tồn tại
vì Minecraft/Xbox Live cần nó để đăng nhập, nhưng nó không "mở khoá" Doppler.

## 🔧 Build Process (Bảo Mật)

```bash
# Developer:
export MC_LAUNCHER_CLIENT_ID="11111111-2222-3333-4444-555555555555"

# Step 1: Embed (obfuscate)
python tools/embed_secret.py --from-env
# → launcher_core/secure_store.py: EMBEDDED_CLIENT_ID = "jk83...=="

# Step 2: Compile
python tools/build.py --from-env
# → PyInstaller compiles, embeds the obfuscated value
# → Output: build_pyinstaller/WizardLauncher/WizardLauncher.exe

# Step 3: Clean
python tools/embed_secret.py --clean
# → launcher_core/secure_store.py: EMBEDDED_CLIENT_ID = ""
# → Plaintext NEVER saved to disk after build

# Step 4: Distribute
# Installer: WizardLauncher-Setup-1.0.0.exe
# (Contains obfuscated Client ID, no secrets)
```

**Lợi ích:**
- plaintext = 0 minutes (exists only during compilation)
- `git log` = no credentials
- `git diff` = no credentials
- `strings .exe` = no GUID, just obfuscated base64
- `decompile` = base64, not credential

## 📦 Distribution & User Experience

### Cho Developer (Phát Hành)
```
1. Build: python tools/build_secure.bat <client-id>
   ✅ Plaintext sanitized
   ✅ obfuscated Client ID in exe
   ✅ Ready to distribute

2. Create installer (Inno Setup)

3. Upload to public URL/store

4. Users download & install
```

### Cho User (Cài & Dùng)
```
1. Download & run installer
   ✅ No special permissions needed
   ✅ No Azure knowledge needed

2. First launch:
   "Go to https://microsoft.com/devicelogin
    Enter code: XXXXXX"
   
   → Authenticates to Microsoft (Minecraft/Xbox Live login only)

3. Subsequent launches:
   ✅ Automatic
   ✅ No login needed
   ✅ Token auto-refreshed

4. App reads secrets from Doppler (independent of the Microsoft login above):
   - Environment first (set by `doppler run`, if used)
   - Doppler API via DOPPLER_TOKEN otherwise
   - Passwords, API keys, tokens
   (All encrypted in transit & at rest)
```

## 🔐 Threat Model

### Threats Mitigated

| Threat | Mitigation | Level |
|--------|-----------|-------|
| Source code leaked (git history) | Embedded at build time only | ✅ Mitigated |
| Binary reverse-engineered | Obfuscated as base64 | ✅ Mitigated |
| String dump `strings .exe` | Not a valid GUID | ✅ Mitigated |
| Disk copied to another machine | DPAPI bound to user+machine | ✅ Mitigated |
| Hard drive stolen | DPAPI bound to machine | ✅ Mitigated |
| Network sniffed | TLS + Microsoft authentication | ✅ Mitigated |
| User forgets password | No passwords stored | ✅ Mitigated |

### Threats NOT Mitigated (Out of Scope)

| Threat | Reason |
|--------|--------|
| Binary patched with debugger | Obfuscation is not encryption; determined attacker can deobfuscate |
| Machine compromised with malware | Credentials are by definition accessible to running process |
| Doppler service token leaked | Token is scoped to one project/config; rotate it in Doppler if compromised |

## 🛠️ Configuration

### Environment Variables

```bash
# Required (build time only)
MC_LAUNCHER_CLIENT_ID=11111111-2222-3333-4444-555555555555

# Optional (runtime, dev override)
DOPPLER_PROJECT=wizard-launcher
DOPPLER_CONFIG=prd
DOPPLER_TOKEN=dp.st.prd.xxxxxxxx   # only needed if not launched via `doppler run`
```

### Config File: launcher_core/config/doppler.json

```json
{
  "doppler": {
    "project": "wizard-launcher",
    "config": "prd",
    "enabled": true
  },
  "secrets": {
    "database_password": "DATABASE_PASSWORD",
    "api_key": "API_KEY"
  }
}
```

## 📋 Checklist untuk Deployment

### Pre-Build
- [ ] Azure app registration created (Microsoft login only)
- [ ] Client ID confirmed (not secret)
- [ ] Doppler project + config created
- [ ] Service token created, scoped to that project/config
- [ ] Test secrets created in Doppler

### Build
- [ ] `python tools/build_secure.bat <client-id>`
- [ ] Check: no plaintext in `launcher_core/secure_store.py`
- [ ] Test exe: `WizardLauncher.exe`
- [ ] Run: Device Code flow works
- [ ] Verify: Can read secrets from Doppler

### Post-Build
- [ ] Create installer
- [ ] Test installer on clean Windows
- [ ] Sign executable (optional but recommended)
- [ ] Upload to distribution channel
- [ ] Document for users (see DOPPLER_SETUP.md)

## 📚 References

- [Doppler CLI](https://docs.doppler.com/docs/cli)
- [Doppler REST API](https://docs.doppler.com/reference/api)
- [Device Code Flow (OAuth 2.0)](https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-device-code)
- [DPAPI (Data Protection API)](https://learn.microsoft.com/en-us/dotnet/standard/security/encrypting-data)
