# Wizard Launcher 2.0

Launcher cho map **Witchcraft and Wizardry**: chạy world Minecraft **1.16.5** ngay trên máy người chơi và nối nó với client **1.20.1** (Fabric + Fabulously Optimized) chỉ bằng một nút Play.

Bản 2.0 viết lại toàn bộ bằng **Kotlin + Java** (bản 1.x là Python/Flet).

## Kiến trúc

```
launcher-app     Kotlin · giao diện Swing + FlatLaf, CLI, đóng gói jpackage
launcher-core    Kotlin · cài đặt, tải file an toàn, đăng nhập, chạy server/client
pack-converter   Kotlin · chuyển resource pack 1.16.5 → 1.20.1 + DSL định nghĩa state
server-host      Java   · 1 JVM chạy cả server 1.16.5 lẫn ViaProxy
client-boot      Java   · khởi động Minecraft mà token không lộ trên command line
```

Bộ cài mang theo Java 17 (jlink). Chính runtime đó chạy launcher, client 1.20.1 và server 1.16.5, nên không bao giờ phải tải hay dò tìm Java.

## Server tốn ít RAM hơn

Bản 1.x chạy **2 JVM**: server và ViaProxy. Bản 2.0 dùng `server-host`: server vanilla được nạp vào một classloader cách ly (vì nó dùng Netty 4.1.25/Log4j 2.8), còn ViaProxy chạy ngay cạnh trong cùng JVM. Hai bên dùng chung heap, JIT và GC.

Kết quả đo thực tế với server 1.16.5 + ViaProxy đi kèm, cùng giới hạn heap 1 GB, lúc nhàn rỗi:

| Cấu hình | RSS |
|---|---|
| 1.x: 2 JVM, cờ JVM cũ | ~1620 MB |
| 2.0: 1 JVM, cờ cũ | ~1408 MB |
| **2.0: 1 JVM, cờ mới** (`JvmFlags.server`) | **~830 MB** |

Các cờ mới cho heap co giãn theo nhu cầu (`-Xms` thấp, Min/MaxHeapFreeRatio, periodic GC của G1) thay vì chiếm sẵn cả heap như `-Xms=-Xmx` + AlwaysPreTouch trước đây. Chúng còn dùng string dedup, ít luồng JIT hơn và code cache nhỏ hơn. Heap mặc định cũng nhỏ lại, có 3 mức Low / Balanced / High, và mức Low giảm luôn view-distance. Server chỉ cần mạng loopback: đã tắt compression và native transport, và tắt kiểm tra cập nhật của ViaProxy (`-DskipUpdateCheck`).

Khi vừa khởi động xong, RSS cao hơn một chút (~1.1–1.2 GB) rồi mới co lại.

Muốn tự kiểm tra trên máy mình: `WizardLauncher --world-selftest`, hoặc vào menu **Tools → Test the world server**. Lệnh này khởi động world, ping bằng protocol 1.20.1, báo RAM rồi tắt và lưu. Không cần internet.

Chế độ 2 JVM cũ vẫn còn để dự phòng: đặt `"mode": "split"` trong catalog.

## Chạy offline

- Sau lần cài đầu, **Play không dùng mạng**. Mỗi bước cài có một "dấu vân tay" trong `install_state.json`; khớp là bỏ qua bước đó.
- Server là world cục bộ ở offline-mode. Người đăng nhập Microsoft mà mất mạng vẫn chơi được bằng profile đã lưu (tên và UUID). Việc refresh token chỉ thử khi có mạng.
- **Settings → Offline mode** cấm hoàn toàn mọi truy cập mạng.
- Để cài cho máy chưa từng có mạng: trên máy đã cài, chọn **Tools → Export offline bundle** (hoặc `--export-bundle file.wizardpack`), rồi trên máy kia chọn **Import offline bundle** (hoặc `--import-bundle`). Bundle chứa manifest SHA-256 cho từng file. Chỉ cần một file sai hoặc một đường dẫn lạ là toàn bộ bundle bị từ chối và máy nhận không bị thay đổi gì. Bundle không mang theo save, settings hay token.
- Server tự tắt và lưu world khi Minecraft thoát (nó theo dõi PID của client). Vì vậy có thể đóng launcher trong lúc chơi mà không cần tiến trình watchdog riêng.

## Resource pack 1.16.5 trên 1.20.1

Pack được viết cho 1.16.5 (`pack_format` 6). Launcher tự chuyển nó sang 1.20.1 (`pack_format` 15) trước khi bật, và ghi báo cáo vào `logs/resource-pack-conversion.txt`. Cũng có thể dùng converter riêng:

```
WizardLauncher --convert-pack "Pack 1.16.5.zip" "Pack 1.20.1.zip" [--rules wizard-states.json]
```

Hoặc trong app: **Tools → Convert a 1.16.5 resource pack**.

| Thay đổi của game từ 1.16.5 đến 1.20.1 | Converter xử lý thế nào |
|---|---|
| 1.19.3: atlas chỉ còn tự gom `block/` và `item/`, nên model dùng texture ở thư mục khác sẽ hiện ô tím-đen | Sinh `atlases/blocks.json`: thêm directory source cho thư mục tùy biến, single source cho texture vanilla khác |
| 1.17: `grass_path` → `dirt_path`, squid chuyển thư mục; 1.19.4: glint tách đôi | Copy file và sửa các tham chiếu |
| 1.17: `cauldron` tách thành `cauldron` + `water_cauldron` | Tách blockstate theo `level`, đổi model `cauldron_levelN` sang tên mới |
| 1.20: xoá font provider `legacy_unicode`. Pack 1.16 hay override `unicode_page_XX.png` làm GUI/icon | Đổi mỗi trang override thành provider `bitmap`: cắt glyph theo `glyph_sizes.bin`, glyph trống có độ rộng thành `space` |
| 1.17: post shader bắt buộc GLSL 150 core | Nâng `#version`, `attribute/varying`, `gl_FragColor`, `texture2D` |
| `pack_format` 6 bị báo "incompatible" | Ghi lại thành 15 |

### Định nghĩa state (`wizard-states.json`)

Các luật được áp theo thứ tự: bảng dựng sẵn 1.16.5→1.20.1, rồi `wizard-states.json` ở gốc pack, rồi `wizard-states.json` trong thư mục dữ liệu launcher (mở qua **Tools → Edit block state rules**).

```json
{
  "format": 1,
  "states": {
    "minecraft:note_block": {
      "instrument=harp,note=1,powered=false": { "model": "wizard:block/crystal_ball" }
    }
  },
  "items": {
    "minecraft:stick": [ { "predicate": { "custom_model_data": 1001 }, "model": "wizard:item/wand" } ]
  },
  "split_blockstates": [
    { "from": "minecraft:cauldron", "when": { "level": "1|2|3" }, "to": "minecraft:water_cauldron" }
  ],
  "rename_references": { "models": { "ns:cu": "ns:moi" }, "textures": {} },
  "copy_files": [ { "from": "assets/...", "to": "assets/..." } ],
  "lang_keys": { "key.cu": "key.moi" }
}
```

- `states`: thêm hoặc ghi đè variant (model) cho từng block state. Dùng được cho cả blockstate dạng variants lẫn multipart.
- `items`: override `custom_model_data` hoặc predicate bất kỳ. Tự sắp theo thứ tự tăng dần, vì Minecraft lấy override khớp cuối cùng.
- `split_blockstates`, `rename_references`, `copy_files`, `lang_keys`: dùng cho các thay đổi giữa phiên bản mà bảng dựng sẵn chưa có.

**Giới hạn còn lại** (converter không tự dịch được, đều ghi vào báo cáo):

- Tính năng riêng của OptiFine (CIT/CTM/sky) phụ thuộc vào mod client.
- Shader dùng hàm fixed-function.
- GUI `smithing.png` đã đổi bố cục trong 1.20.
- Glyph unicode có độ rộng lẻ có thể lệch khoảng cách tối đa 1px.

## Bảo mật

- **Tải file:** chỉ HTTPS, TLS 1.2+. Redirect được tự theo từng bước và mỗi bước đều kiểm tra allow-list domain riêng cho từng loại nội dung (Mojang/Fabric, Modrinth, Hugging Face). File chỉ xuất hiện sau khi hash khớp: SHA-1 của Mojang, SHA-512 của Modrinth, SHA-256 trong catalog. Có giới hạn kích thước, và ghi file theo kiểu atomic.
- **Zip:** chặn Zip Slip, chặn zip bomb, và giải nén vào thư mục tạm rồi mới đổi chỗ.
- **Catalog đã ký:** có thể thay `catalog.json` bằng bản mới đặt trong thư mục dữ liệu, nhưng chỉ khi có chữ ký Ed25519 (`catalog.json.sig`) khớp khóa công khai đi kèm bản build. Dùng `--catalog-keygen` và `--catalog-sign` để tạo khóa và ký.
- **Token:**
  - Refresh token lưu bằng DPAPI (Windows), Keychain (macOS) hoặc Secret Service (Linux). Nếu không có các dịch vụ đó thì dùng file AES-GCM kèm khóa 0600.
  - Access token chỉ nằm trong RAM. Nó được truyền cho Minecraft qua stdin nhờ `client-boot`, nên **không xuất hiện trên command line** (có test kiểm chứng).
  - Log luôn che token.
- **Jar đi kèm:** `server.jar` và `ViaProxy.jar` được so SHA-256 với catalog trước khi chạy.
- **Server:**
  - Chỉ nghe trên loopback. RCON, query và JMX đều tắt.
  - Bật whitelist theo UUID offline của người chơi. Khi bật LAN thì whitelist tắt, và chỉ bridge được mở ra mạng LAN.
  - Port mặc định bị chiếm thì tự chọn port trống.
- **Log4Shell:** `server.jar` vanilla 1.16.5 dùng Log4j 2.8.1. Với phiên bản này, cờ `-Dlog4j2.formatMsgNoLookups=true` mà bản 1.x dùng **không có tác dụng**. Bản 2.0 dùng cấu hình `%msg{nolookups}` (cách Mojang tự vá cho 1.12–1.16.5) và thêm các cờ JNDI/RMI hardening.
- **Tiến trình:** nhận diện bằng PID kết hợp thời điểm khởi động, nên không bao giờ kill nhầm tiến trình lạ có PID trùng. Chỉ chạy một launcher tại một thời điểm.

## Build

Cần JDK 17 trở lên.

```
./gradlew build                                   # compile + toàn bộ test
./gradlew :launcher-app:run                       # chạy thử
./gradlew :launcher-app:jpackage                  # app-image (kèm Java runtime)
./gradlew :launcher-app:jpackage -PjpackageType=dmg   # macOS
```

Bộ cài Windows dùng `installer/WizardLauncher.iss` (Inno Setup) với app-image làm đầu vào. CI (`.github/workflows/build.yml`) chạy test ở mọi push, và build bộ cài cho cả 3 hệ điều hành khi có tag `v*.*.*`.

Đăng nhập Microsoft cần Azure client id (public). Cung cấp nó qua biến `MC_LAUNCHER_CLIENT_ID` lúc build, hoặc secret cùng tên trên GitHub. Nếu không có, người chơi dùng tên offline.

## Dữ liệu

Vị trí thư mục dữ liệu giống bản 1.x, nên world và progress cũ được giữ nguyên:

- Windows: `%LOCALAPPDATA%\WizardLauncher`
- macOS: `~/Library/Application Support/WizardLauncher`
- Linux: `$XDG_DATA_HOME/WizardLauncher`

Biến `WIZARD_LAUNCHER_DATA` dùng để đổi thư mục này, tiện khi test hoặc làm bản portable.

Created by Foxy (.phungminh)
