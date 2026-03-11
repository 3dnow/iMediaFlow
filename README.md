📱 iOS Media Flow (iMediaFlow)


iOS Media Flow is a high-performance, open-source desktop application designed to manage, preview, and export photos and videos from iOS devices directly via USB, without the need for iTunes.

Built with PySide6 and pymobiledevice3, it is specifically optimized for handling massive media libraries (10,000+ files) and large 4K videos without crashing or causing memory overflows.

✨ Key Features

🚫 No iTunes Required: Communicates directly with iOS devices using the AFC (Apple File Conduit) protocol via USB.

⚡ Deterministic Viewport Rendering: Dynamically generates thumbnails only for the items currently visible on your screen. It handles 10,000+ files instantly without clogging the IO queue.

🛡️ Smart Memory Protection: * Reads only the "head" of video files (e.g., first 30MB) using OpenCV to extract thumbnails, preventing 4K video files from blowing up your RAM.

Chunks large file transfers (5MB per chunk) to ensure stable exports.

🔄 API Compatibility Layer (RobustAfcClient): Automatically adapts to different versions of pymobiledevice3 (falling back seamlessly between open(), file_open(), and pull), ensuring the app runs perfectly regardless of dependency updates.

🕒 Original Timestamp Sync: Solves the classic Windows copy issue by perfectly restoring the original iOS st_mtime (modified time) to exported files.

🗂️ Dual-Pane Task Manager: Innovative "Source Library -> Task Queue" dual-pane UI. Select what you need, save/load your task lists, and export everything in one click.

🖼️ HEIC & RAW Support: Native support for rendering HEIC photos and videos via pillow-heif and opencv-python.

🛠️ Prerequisites

pip install PySide6 pymobiledevice3 Pillow pillow-heif opencv-python


🚀 Usage

Connect your unlocked iPhone/iPad to your computer via USB.

Trust the computer on your iOS device.

Run the application:

python iMediaFlow.py


Click "Scan" to load the DCIM directory. Use the search bar to jump to specific files.

Move files to the right-pane Task List and click "Export".

🧠 Architecture Highlights

Thread Isolation: Thumbnail rendering, background scanning, and file exporting run on completely isolated threads and AFC connection instances to avoid USB race conditions (Error 13: Permission Denied).

UI Hard-Locks: To guarantee absolute stability during high-load IO operations (like batch exporting), the UI automatically locks interaction to prevent event loop bottlenecks and Python GC crashes.

<h2 id="中文说明">🇨🇳 中文说明</h2>

iOS Media Flow 是一款极速、稳定的开源桌面应用，用于通过 USB 直接管理、预览和导出 iOS 设备上的照片与视频，完全不需要安装 iTunes。

基于 PySide6 和 pymobiledevice3 开发。我们针对“海量媒体库（上万张照片）”和“超大4K视频”进行了极致的底层优化，彻底解决了同类 Python 脚本常遇到的内存溢出（OOM）、假死、UI卡顿等致命问题。

✨ 核心特色

🚫 摆脱 iTunes： 直接通过底层的 AFC 协议与 iPhone 通信，即插即用。

⚡ 确定性视口渲染 (Viewport Rendering)： 只加载屏幕“当前可见”范围内的缩略图。无论相册有100张还是10000张，内存占用和响应速度始终如一。

🛡️ 内存防爆机制：

视频截帧优化：摒弃将整个视频读入内存的危险做法，只读取视频头部（30MB）并利用 OpenCV 瞬间截取帧，绝不爆内存。

流式导出：强制采用 5MB 物理分块传输（Chunked Transfer），稳如泰山。

🔄 无缝 API 兼容层 (RobustAfcClient)： 抹平了 pymobiledevice3 不同版本间的接口差异。自动探测环境并切换 open() (上下文)、file_open() (句柄) 或 pull 策略，让代码拥有极强的生命力。

🕒 完美时间戳同步： 解决了 Windows 传统复制会导致“修改时间变成今天”的痛点，导出时精确恢复照片/视频在 iPhone 上的原始修改时间。

🗂️ 双栏任务驱动： 采用“左侧资源库 -> 右侧任务表”的专业工作流。支持精准搜索定位、任务列表的保存与加载，批量导出更加清晰可控。

🖼️ 全格式支持： 完美预览 HEIC、DNG 等苹果专属格式以及 MOV/MP4 视频。

🛠️ 依赖与安装

请确保您的环境中已安装以下库：

pip install PySide6 pymobiledevice3 Pillow pillow-heif opencv-python

🚀 如何使用

使用 USB 数据线连接 iPhone 并解锁屏幕，点击“信任此电脑”。

运行主程序：

python iMediaFlow.py


点击 “刷新/扫描” 按钮，程序将瞬间秒刷出整个 DCIM 目录结构。

滚动列表或使用搜索框定位，程序将自动在后台极速加载当前区域的缩略图。

将需要的照片加入右侧 “待导出任务” 列表，点击导出即可。

🧠 技术解析：我们为什么这么稳？

通道隔离 (Channel Isolation)： UI主线程、扫描线程、传输线程与缩略图线程分别拥有独立的 AfcService 连接通道。彻底消灭了多线程抢占同一 USB 句柄导致的死锁和异常中断。

状态硬锁 (UI Hard-Lock)： 在进行大文件预览或批量导出等密集型 IO 操作时，程序采用物理锁死策略（屏蔽一切多余交互）。牺牲一点点花哨的异步交互，换来 100% 不崩溃的确定性。

滚动防抖与残余清理： 采用定时器防抖 + 强制打断机制，用户每次滑动或跳转都会清空旧的无效队列，算力永远只服务于“眼前的画面”。

🤝 Contributing / 贡献

欢迎提交 Issue 和 Pull Request！如果你觉得这个工具解决了你的痛点，请点一个 ⭐️ Star！

📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
