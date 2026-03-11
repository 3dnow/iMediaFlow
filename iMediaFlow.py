import sys
import os
import time
import io
import threading
import tempfile
from threading import Lock

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QListWidget, QListWidgetItem, 
                               QLabel, QMessageBox, QProgressBar, QAbstractItemView,
                               QFileDialog, QLineEdit)
from PySide6.QtCore import Qt, QSize, Signal, QThread, QTimer, QPoint
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QImage

# === 依赖库 ===
try:
    from PIL import Image, ImageOps
except ImportError:
    pass

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HAS_HEIF = True
except ImportError:
    HAS_HEIF = False

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

try:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.afc import AfcService
except ImportError:
    print("缺失依赖: pymobiledevice3")
    sys.exit(1)

# 配置
TEMP_VIEW_DIR = r"D:\temp"
EXPORT_DIR = r"C:\output"
os.makedirs(TEMP_VIEW_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# 主操作锁
MAIN_OP_LOCK = Lock()

# ================= 核心：全兼容 AFC 客户端 =================
class RobustAfcClient:
    def __init__(self, afc_service):
        self.afc = afc_service

    def get_file_size(self, path):
        try:
            info = self.afc.stat(path)
            return int(info.get('st_size', 0))
        except: return 0

    def get_file_mtime(self, path):
        try:
            info = self.afc.stat(path)
            raw = info.get('st_mtime', time.time())
            if hasattr(raw, 'timestamp'): return raw.timestamp()
            return float(raw)
        except: return time.time()

    def get_file_bytes_head(self, path, limit_bytes=0):
        # 策略 A
        if hasattr(self.afc, 'open'):
            try:
                with self.afc.open(path, 'rb') as f:
                    if limit_bytes > 0: return f.read(limit_bytes)
                    return f.read()
            except: pass

        # 策略 B
        opener = getattr(self.afc, 'file_open', None) or getattr(self.afc, 'fopen', None)
        reader = getattr(self.afc, 'file_read', None) or getattr(self.afc, 'fread', None)
        closer = getattr(self.afc, 'file_close', None) or getattr(self.afc, 'fclose', None)
        
        if opener and reader and closer:
            handle = None
            try:
                handle = opener(path, 'r')
                if limit_bytes > 0:
                    return reader(handle, limit_bytes)
                else:
                    buffer = io.BytesIO()
                    while True:
                        chunk = reader(handle, 1024*1024*5)
                        if not chunk: break
                        buffer.write(chunk)
                    return buffer.getvalue()
            except: pass
            finally:
                if handle: 
                    try: closer(handle)
                    except: pass
        
        # 策略 C (Fallback)
        if hasattr(self.afc, 'get_file_contents'):
            try:
                size = self.get_file_size(path)
                # 只有小文件才尝试全读
                if limit_bytes == 0 or size < limit_bytes:
                    return self.afc.get_file_contents(path)
            except: pass
            
        return None

    def read_file_chunked(self, remote_path, callback_write):
        if hasattr(self.afc, 'open'):
            with self.afc.open(remote_path, 'rb') as f:
                while True:
                    chunk = f.read(1024*1024*5)
                    if not chunk: break
                    callback_write(chunk)
            return True

        opener = getattr(self.afc, 'file_open', None) or getattr(self.afc, 'fopen', None)
        reader = getattr(self.afc, 'file_read', None) or getattr(self.afc, 'fread', None)
        closer = getattr(self.afc, 'file_close', None) or getattr(self.afc, 'fclose', None)

        if opener and reader and closer:
            handle = opener(remote_path, 'r')
            try:
                while True:
                    chunk = reader(handle, 1024*1024*5)
                    if not chunk: break
                    callback_write(chunk)
                return True
            finally:
                closer(handle)
        
        if hasattr(self.afc, 'pull'):
            return False 

        return False

# ================= 辅助：图标生成 =================
def generate_video_icon_placeholder():
    size = 140
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(40, 40, 40)) 
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(Qt.white)
    painter.setPen(Qt.NoPen)
    for i in range(0, size, 20):
        painter.drawRect(5, i + 5, 10, 10)
        painter.drawRect(size - 15, i + 5, 10, 10)
    painter.setBrush(QColor(147, 112, 219)) 
    painter.drawRect(25, 20, size - 50, size - 40)
    painter.setBrush(Qt.white)
    center_x, center_y = size // 2, size // 2
    triangle = [(center_x - 15, center_y - 15), (center_x - 15, center_y + 15), (center_x + 20, center_y)]
    painter.drawPolygon([QPoint(*p) for p in triangle])
    painter.end()
    return QIcon(pixmap)

def generate_text_icon(text):
    pixmap = QPixmap(140, 140)
    pixmap.fill(QColor(60, 60, 60)) 
    painter = QPainter(pixmap)
    painter.setPen(QColor(200, 200, 200))
    font = painter.font()
    font.setPointSize(20)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
    painter.end()
    return QIcon(pixmap)

VIDEO_ICON_DEFAULT = None 

# ================= 辅助：后台清理 =================
class CleanerThread(QThread):
    def run(self):
        while not self.isInterruptionRequested():
            try:
                now = time.time()
                if os.path.exists(TEMP_VIEW_DIR):
                    for fname in os.listdir(TEMP_VIEW_DIR):
                        fpath = os.path.join(TEMP_VIEW_DIR, fname)
                        try:
                            if now - os.path.getmtime(fpath) < 100: continue
                            os.remove(fpath)
                        except OSError: pass 
            except: pass
            self.sleep(200)

# ================= 批处理缩略图 Worker (硬限制版) =================
class BatchThumbnailWorker(QThread):
    progress = Signal(int, int) # cur, total
    item_ready = Signal(str, QIcon)
    finished_batch = Signal()
    
    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks 
        self.lockdown = None
        self.afc = None
        self.client = None

    def connect_device(self):
        try:
            if self.lockdown:
                try: self.lockdown.close()
                except: pass
            self.lockdown = create_using_usbmux()
            self.afc = AfcService(self.lockdown)
            self.client = RobustAfcClient(self.afc)
            return True
        except: 
            self.client = None
            return False

    def process_image(self, data):
        try:
            image = Image.open(io.BytesIO(data))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((140, 140), Image.Resampling.LANCZOS)
            if image.mode != "RGBA": image = image.convert("RGBA")
            qimg = QImage(image.tobytes("raw", "RGBA"), image.size[0], image.size[1], QImage.Format_RGBA8888)
            return QIcon(QPixmap.fromImage(qimg))
        except: return None

    def process_video(self, data):
        if not HAS_OPENCV: return None
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(data)
                tmp_name = tmp.name
            
            cap = cv2.VideoCapture(tmp_name)
            if not cap.isOpened(): return None
                
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(frame)
                image.thumbnail((140, 140), Image.Resampling.LANCZOS)
                qimg = QImage(image.tobytes("raw", "RGB"), image.size[0], image.size[1], QImage.Format_RGB888)
                pix = QPixmap.fromImage(qimg)
                painter = QPainter(pix)
                painter.setBrush(QColor(0,0,0,128))
                painter.setPen(Qt.NoPen)
                painter.drawRect(0, 110, 140, 30)
                painter.setPen(Qt.white)
                painter.drawText(0, 110, 140, 30, Qt.AlignCenter, "VIDEO")
                painter.end()
                return QIcon(pix)
        except: pass
        finally:
            if tmp_name:
                try: os.remove(tmp_name)
                except: pass
        return None

    def run(self):
        if not self.tasks:
            self.finished_batch.emit()
            return

        # 初次连接
        if not self.connect_device():
            time.sleep(1)
            if not self.connect_device():
                self.finished_batch.emit()
                return

        total = len(self.tasks)
        
        for i, (path, fname, is_video) in enumerate(self.tasks):
            if self.isInterruptionRequested(): break
            
            self.progress.emit(i + 1, total)
            
            # 确保连接活跃
            if not self.client:
                if not self.connect_device():
                    continue # 跳过当前，尝试下一个

            try:
                # 图片限制 50MB，视频尝试读前 30MB
                limit = 100 * 1024 * 1024 if is_video else 50 * 1024 * 1024
                
                size = self.client.get_file_size(path)
                if size < 200 * 1024 and is_video:
                    # 大图片直接跳过
                    continue

                if size > limit and not is_video:
                    # 大图片直接跳过
                    continue
                
                raw_data = None
                if is_video:
                    # 视频读头
                    raw_data = self.client.get_file_bytes_head(path, 100 * 1024 * 1024)
                else:
                    # 图片全读 (受限于 50MB 检查)
                    raw_data = self.client.get_file_bytes_head(path, 0)
                
                if raw_data:
                    icon = None
                    if is_video: icon = self.process_video(raw_data)
                    else: icon = self.process_image(raw_data)
                    
                    if icon:
                        self.item_ready.emit(path, icon)
            
            except Exception as e:
                # 遇到错误，打印并重置连接
                print(f"Thumb Err [{fname}]: {e}")
                self.client = None
                time.sleep(0.5) # 冷却一下

        self.finished_batch.emit()

# ================= 扫描线程 =================
class ScanWorker(QThread):
    item_found = Signal(str, str) 
    finished = Signal(int)
    error = Signal(str)

    def run(self):
        try:
            lockdown = create_using_usbmux()
            afc = AfcService(lockdown)
            root = '/DCIM'
            count = 0
            
            with MAIN_OP_LOCK:
                try: folders = afc.listdir(root)
                except Exception as e:
                    self.error.emit(f"连接失败: {e}")
                    return
            
            folders.sort(reverse=True)
            for folder in folders:
                if folder.startswith('.'): continue
                f_path = f"{root}/{folder}"
                
                with MAIN_OP_LOCK:
                    try:
                        if afc.stat(f_path).get('st_ifmt') != 'S_IFDIR': continue
                        files = afc.listdir(f_path)
                    except: continue
                
                files.sort(reverse=True)
                for fname in files:
                    if fname.startswith('.'): continue
                    ext = fname.lower().split('.')[-1]
                    if ext in ['jpg', 'jpeg', 'png', 'heic', 'mov', 'mp4']:
                        full_path = f"{f_path}/{fname}"
                        self.item_found.emit(fname, full_path)
                        count += 1
            self.finished.emit(count)
        except Exception as e: self.error.emit(str(e))

# ================= 传输线程 =================
class TransferWorker(QThread):
    progress_update = Signal(int, str) 
    finished = Signal(str) 
    error = Signal(str)

    def __init__(self, mode, files, dest):
        super().__init__()
        self.mode = mode
        self.files = files
        self.dest = dest

    def run(self):
        try:
            lockdown = create_using_usbmux()
            afc = AfcService(lockdown)
            client = RobustAfcClient(afc)
            total = len(self.files)
            
            for idx, (remote_path, fname) in enumerate(self.files):
                local_path = os.path.join(self.dest, fname)
                file_size = 0
                
                with MAIN_OP_LOCK:
                    file_size = client.get_file_size(remote_path)

                self.progress_update.emit(0, f"正在下载: {fname}")
                
                success = False
                
                def write_cb(chunk):
                    with open(local_path, 'ab') as f: f.write(chunk)
                
                with open(local_path, 'wb') as f: pass
                
                transferred = 0
                def prog_cb(chunk):
                    nonlocal transferred
                    with open(local_path, 'ab') as f: f.write(chunk)
                    transferred += len(chunk)
                    if file_size > 0:
                        pct = int((transferred / file_size) * 100)
                        self.progress_update.emit(pct, f"下载中 {pct}%: {fname}")

                with MAIN_OP_LOCK:
                    try:
                        if client.read_file_chunked(remote_path, prog_cb):
                            success = True
                        elif hasattr(afc, 'pull'):
                            afc.pull(remote_path, local_path)
                            success = True
                    except Exception as e:
                        if self.mode == 'preview':
                            self.error.emit(f"错误: {e}")
                            return
                
                if success:
                    try:
                        ts = client.get_file_mtime(remote_path)
                        if ts > 1e11: ts /= 1e9
                        os.utime(local_path, (ts, ts))
                    except: pass

                self.progress_update.emit(100, "完成")
                
                if self.mode == 'preview':
                    if success:
                        time.sleep(0.1)
                        self.finished.emit(local_path)
                        return
                    else:
                        self.error.emit("下载失败")
                        return

            self.finished.emit("ALL_DONE")

        except Exception as e:
            self.error.emit(str(e))

# ================= 主窗口 =================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iOS 媒体流管理 (V19 视口修正版)")
        self.resize(1300, 800)
        
        global VIDEO_ICON_DEFAULT
        VIDEO_ICON_DEFAULT = generate_video_icon_placeholder()
        self.list_items_map = {} 
        self.filename_to_items = {} 
        self.scan_count = 0
        self.added_paths = set()
        self.preview_lock = False 
        self.batch_worker = None 

        # === 布局 ===
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        container = QVBoxLayout(main_widget)
        h_layout = QHBoxLayout()
        
        # [左]
        left_panel = QWidget()
        left_box = QVBoxLayout(left_panel)
        left_box.setContentsMargins(0,0,0,0)
        
        search_box = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("定位文件名...")
        self.search_input.returnPressed.connect(self.search_and_jump)
        self.btn_search = QPushButton("定位")
        self.btn_search.clicked.connect(self.search_and_jump)
        search_box.addWidget(self.search_input)
        search_box.addWidget(self.btn_search)
        left_box.addLayout(search_box)
        
        top_ctrl = QHBoxLayout()
        self.btn_scan = QPushButton("刷新/扫描")
        self.btn_scan.clicked.connect(self.start_scan)
        top_ctrl.addWidget(self.btn_scan)
        self.lbl_info = QLabel("未连接")
        top_ctrl.addWidget(self.lbl_info)
        left_box.addLayout(top_ctrl)
        
        self.list_source = QListWidget()
        self.list_source.setViewMode(QListWidget.IconMode)
        self.list_source.setIconSize(QSize(120, 120))
        self.list_source.setSpacing(6)
        self.list_source.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_source.setUniformItemSizes(True)
        self.list_source.setLayoutMode(QListWidget.Batched)
        self.list_source.setBatchSize(50)
        self.list_source.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.list_source.doubleClicked.connect(self.on_preview)
        left_box.addWidget(self.list_source)
        
        # [中]
        mid_panel = QWidget()
        mid_box = QVBoxLayout(mid_panel)
        mid_box.setAlignment(Qt.AlignCenter)
        self.btn_add = QPushButton(">>")
        self.btn_add.setFixedSize(40, 60)
        self.btn_add.clicked.connect(self.add_task)
        self.btn_remove = QPushButton("<<")
        self.btn_remove.setFixedSize(40, 60)
        self.btn_remove.clicked.connect(self.remove_task)
        mid_box.addWidget(self.btn_add)
        mid_box.addSpacing(10)
        mid_box.addWidget(self.btn_remove)
        
        # [右]
        right_panel = QWidget()
        right_panel.setFixedWidth(280)
        right_box = QVBoxLayout(right_panel)
        right_box.setContentsMargins(0,0,0,0)
        
        r_tools = QHBoxLayout()
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_tasks)
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.save_list)
        self.btn_load = QPushButton("加载")
        self.btn_load.clicked.connect(self.load_list)
        r_tools.addWidget(self.btn_clear)
        r_tools.addWidget(self.btn_save)
        r_tools.addWidget(self.btn_load)
        right_box.addLayout(r_tools)
        
        self.list_target = QListWidget()
        self.list_target.setSelectionMode(QAbstractItemView.ExtendedSelection)
        right_box.addWidget(self.list_target)
        
        self.btn_export = QPushButton("开始导出 (0)")
        self.btn_export.setMinimumHeight(45)
        self.btn_export.setStyleSheet("background-color: #0078d7; color: white; font-weight: bold;")
        self.btn_export.clicked.connect(self.start_export)
        self.btn_export.setEnabled(False)
        right_box.addWidget(self.btn_export)
        
        h_layout.addWidget(left_panel, 1)
        h_layout.addWidget(mid_panel)
        h_layout.addWidget(right_panel)
        container.addLayout(h_layout)
        
        self.lbl_status = QLabel("就绪")
        self.prog = QProgressBar()
        self.prog.setTextVisible(True)
        container.addWidget(self.lbl_status)
        container.addWidget(self.prog)

        self.cleaner = CleanerThread()
        self.cleaner.start()
        
        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self.trigger_batch_loading)

    def set_ui_locked(self, locked):
        d = not locked
        self.list_source.setEnabled(d)
        self.list_target.setEnabled(d)
        self.btn_scan.setEnabled(d)
        self.btn_add.setEnabled(d)
        self.btn_remove.setEnabled(d)
        self.btn_export.setEnabled(d)
        self.btn_clear.setEnabled(d)
        self.btn_save.setEnabled(d)
        self.btn_load.setEnabled(d)
        self.search_input.setEnabled(d)
        self.btn_search.setEnabled(d)
        if locked: self.lbl_status.setStyleSheet("color: #ff5555; font-weight: bold;")
        else: self.lbl_status.setStyleSheet("")

    def start_scan(self):
        self.list_source.clear()
        self.list_items_map.clear()
        self.filename_to_items.clear()
        self.scan_count = 0
        self.set_ui_locked(True)
        self.lbl_info.setText("扫描中...")
        self.prog.setValue(0)
        self.scanner = ScanWorker()
        self.scanner.item_found.connect(self.add_source_item)
        self.scanner.finished.connect(self.scan_done)
        self.scanner.error.connect(lambda e: QMessageBox.critical(self, "错", e))
        self.scanner.start()

    def add_source_item(self, fname, full_path):
        ext = fname.lower().split('.')[-1]
        icon = VIDEO_ICON_DEFAULT if ext in ['mov', 'mp4', 'm4v'] else generate_text_icon(ext.upper())
        item = QListWidgetItem(icon, fname)
        item.setData(Qt.UserRole, full_path)
        item.setData(Qt.UserRole + 1, fname)
        item.setData(Qt.UserRole + 2, "pending")
        self.list_source.addItem(item)
        self.list_items_map[full_path] = item
        self.filename_to_items[fname.lower()] = item
        self.scan_count += 1
        if self.scan_count % 100 == 0: self.lbl_info.setText(f"{self.scan_count}")

    def scan_done(self, count):
        self.set_ui_locked(False)
        self.lbl_info.setText(f"共 {count} 个")
        # 扫描完不自动加载，等待用户交互 (Lazy Start)

    def search_and_jump(self):
        text = self.search_input.text().strip().lower()
        if not text: return
        target_item = None
        for name, item in self.filename_to_items.items():
            if text in name:
                target_item = item
                break
        if target_item:
            self.list_source.scrollToItem(target_item, QAbstractItemView.PositionAtTop)
            self.list_source.setCurrentItem(target_item)
            self.trigger_batch_loading()
            self.lbl_status.setText(f"定位: {target_item.text()}")
        else: QMessageBox.information(self, "无", "未找到")

    def on_scroll(self): 
        self.scroll_timer.start(500)
    
    def trigger_batch_loading(self):
        # 1. 计算视口
        top = self.list_source.itemAt(10, 10)
        start = self.list_source.row(top) if top else 0
        h = self.list_source.viewport().height()
        
        tasks = []
        count_added = 0
        SAFETY_CAP = 300 # 硬限制：一次最多80张，防卡死
        
        for i in range(start, self.list_source.count()):
            if count_added >= SAFETY_CAP: break # 达到上限强行停止
            
            item = self.list_source.item(i)
            rect = self.list_source.visualItemRect(item)
            
            # 布局未完成保护
            if not rect.isValid(): continue
            
            # 越界检查
            if rect.top() > h: break
            
            if item.data(Qt.UserRole + 2) == "pending":
                path = item.data(Qt.UserRole)
                fname = item.data(Qt.UserRole + 1)
                is_video = fname.lower().endswith(('.mov', '.mp4', '.m4v'))
                tasks.append((path, fname, is_video))
                item.setData(Qt.UserRole + 2, "processing")
                count_added += 1
        
        if not tasks: return

        # 2. 锁死并启动
        self.set_ui_locked(True)
        self.lbl_status.setText(f"加载缩略图 ({len(tasks)}个)...")
        self.prog.setValue(0)
        
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.requestInterruption()
            self.batch_worker.wait()
            
        self.batch_worker = BatchThumbnailWorker(tasks)
        self.batch_worker.progress.connect(self.on_thumb_progress)
        self.batch_worker.item_ready.connect(self.update_icon)
        self.batch_worker.finished_batch.connect(self.on_batch_done)
        self.batch_worker.start()

    def on_thumb_progress(self, cur, total):
        self.prog.setValue(int(cur/total * 100))
        self.lbl_status.setText(f"加载缩略图: {cur}/{total}")

    def on_batch_done(self):
        self.set_ui_locked(False)
        self.lbl_status.setText("就绪")
        self.prog.setValue(100)

    def update_icon(self, path, icon):
        item = self.list_items_map.get(path)
        if item:
            item.setIcon(icon)
            item.setData(Qt.UserRole + 2, "loaded")

    def on_preview(self, idx):
        if self.preview_lock: return
        self.preview_lock = True
        QTimer.singleShot(2000, lambda: setattr(self, 'preview_lock', False))
        
        item = self.list_source.currentItem()
        if not item: return
        fname = item.data(Qt.UserRole + 1)
        path = item.data(Qt.UserRole)
        
        self.set_ui_locked(True)
        self.lbl_status.setText(f"下载预览: {fname}")
        self.prog.setValue(0)
        
        self.viewer = TransferWorker('preview', [(path, fname)], TEMP_VIEW_DIR)
        self.viewer.progress_update.connect(self.update_progress)
        self.viewer.finished.connect(self.on_preview_ready)
        self.viewer.error.connect(self.on_transfer_error)
        self.viewer.start()

    def on_preview_ready(self, local_path):
        self.set_ui_locked(False)
        self.lbl_status.setText("打开中...")
        if local_path and os.path.exists(local_path):
            try: os.startfile(local_path)
            except Exception as e: QMessageBox.warning(self, "错误", f"无法打开: {e}")

    def update_progress(self, val, msg):
        self.prog.setValue(val)
        self.lbl_status.setText(msg)

    def on_transfer_error(self, err):
        self.set_ui_locked(False)
        self.lbl_status.setText("错误")
        QMessageBox.critical(self, "错误", err)

    def add_task(self):
        for item in self.list_source.selectedItems():
            path = item.data(Qt.UserRole)
            fname = item.data(Qt.UserRole + 1)
            if path in self.added_paths: continue
            self.added_paths.add(path)
            t_item = QListWidgetItem(f"{fname}")
            t_item.setIcon(item.icon())
            t_item.setData(Qt.UserRole, path)
            t_item.setData(Qt.UserRole + 1, fname)
            self.list_target.addItem(t_item)
        self.update_export_btn()

    def remove_task(self):
        for item in self.list_target.selectedItems():
            self.added_paths.remove(item.data(Qt.UserRole))
            self.list_target.takeItem(self.list_target.row(item))
        self.update_export_btn()

    def clear_tasks(self):
        self.list_target.clear()
        self.added_paths.clear()
        self.update_export_btn()

    def update_export_btn(self):
        count = self.list_target.count()
        self.btn_export.setText(f"开始导出 ({count})")
        self.btn_export.setEnabled(count > 0)

    def save_list(self):
        if self.list_target.count() == 0: return
        p, _ = QFileDialog.getSaveFileName(self, "保存", "", "Txt (*.txt)")
        if not p: return
        with open(p, 'w', encoding='utf-8') as f:
            for i in range(self.list_target.count()):
                it = self.list_target.item(i)
                f.write(f"{it.data(Qt.UserRole)}|{it.data(Qt.UserRole+1)}\n")

    def load_list(self):
        p, _ = QFileDialog.getOpenFileName(self, "加载", "", "Txt (*.txt)")
        if not p: return
        self.clear_tasks()
        try:
            with open(p, 'r', encoding='utf-8') as f:
                for line in f:
                    if '|' not in line: continue
                    path, fname = line.strip().split('|', 1)
                    self.added_paths.add(path)
                    item = QListWidgetItem(fname)
                    if path in self.list_items_map: item.setIcon(self.list_items_map[path].icon())
                    item.setData(Qt.UserRole, path)
                    item.setData(Qt.UserRole + 1, fname)
                    self.list_target.addItem(item)
            self.update_export_btn()
        except: pass

    def start_export(self):
        files = []
        for i in range(self.list_target.count()):
            it = self.list_target.item(i)
            files.append((it.data(Qt.UserRole), it.data(Qt.UserRole + 1)))
        
        self.set_ui_locked(True)
        self.lbl_status.setText("批量导出中...")
        self.prog.setValue(0)
        
        self.exporter = TransferWorker('export', files, EXPORT_DIR)
        self.exporter.progress_update.connect(self.update_progress)
        self.exporter.finished.connect(self.on_export_done)
        self.exporter.error.connect(self.on_transfer_error)
        self.exporter.start()

    def on_export_done(self, msg):
        self.set_ui_locked(False)
        self.lbl_status.setText("导出完成")
        QMessageBox.information(self, "完成", f"已导出到: {EXPORT_DIR}")
        os.startfile(EXPORT_DIR)

    def closeEvent(self, e):
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.requestInterruption()
            self.batch_worker.wait()
        if self.cleaner.isRunning(): self.cleaner.requestInterruption()
        e.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget { background-color: #2b2b2b; color: #ddd; font-family: "Microsoft YaHei"; }
        QListWidget { background-color: #333; border: 1px solid #444; border-radius: 4px; }
        QListWidget::item:selected { background-color: #0078d7; }
        QPushButton { background-color: #444; border: 1px solid #555; padding: 5px; border-radius: 4px; }
        QPushButton:hover { background-color: #555; }
        QPushButton:disabled { background-color: #333; color: #666; }
        QProgressBar { text-align: center; border: 1px solid #444; height: 20px; border-radius: 4px; }
        QProgressBar::chunk { background-color: #0078d7; }
        QLineEdit { background-color: #333; border: 1px solid #555; padding: 4px; color: #ddd; border-radius: 4px; }
    """)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

