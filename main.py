import json
import os
import sys
import threading
import time
import traceback
import shutil
from datetime import datetime
from functools import partial
from pathlib import Path
from urllib.parse import unquote

#
# Windows 中文输入法候选框不显示：通常需要启用 SDL 的 IME UI 提示。
# 必须在 Kivy 初始化之前设置环境变量。
#
os.environ.setdefault("SDL_HINT_IME_SHOW_UI", "1")

from kivy.config import Config
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.utils import platform

# 桌面本机调试时，强制使用系统键盘/IME，提升中文输入可用性。
# Android 上不强制该参数，避免部分设备出现输入子系统兼容问题。
if platform != "android":
    Config.set("kivy", "keyboard_mode", "system")

from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import BooleanProperty, StringProperty
from kivy.core.text import LabelBase
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.image import AsyncImage
from kivy.uix.widget import Widget
from kivymd.app import MDApp
from kivymd.uix.button import MDRaisedButton, MDTextButton
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.list import TwoLineAvatarIconListItem, IconLeftWidget
from kivymd.uix.snackbar import MDSnackbar

from api_client import LLMApiClient
from storage import LocalStorage

try:
    from plyer import camera
except Exception:
    camera = None

try:
    from plyer import filechooser
except Exception:
    filechooser = None

try:
    from android.permissions import Permission, request_permissions, check_permission
except Exception:
    Permission = None
    request_permissions = None
    check_permission = None

try:
    from android import activity as android_activity
except Exception:
    android_activity = None

try:
    from jnius import autoclass
except Exception:
    autoclass = None


KV_FILE = "ui.kv"
CONFIG_FILE = "app_config.json"
CRASH_LOG_FILE = "last_crash.log"


def _write_crash_log(exc_type, exc_value, exc_tb):
    """记录未捕获异常，便于定位 Android 闪退。"""
    try:
        content = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        crash_path = Path(CRASH_LOG_FILE)
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app and getattr(app, "user_data_dir", ""):
                crash_path = Path(app.user_data_dir) / CRASH_LOG_FILE
        except Exception:
            pass
        crash_path.parent.mkdir(parents=True, exist_ok=True)
        crash_path.write_text(content, encoding="utf-8")
    except Exception:
        pass


sys.excepthook = _write_crash_log


class HistoryItem(TwoLineAvatarIconListItem):
    record_id = StringProperty("")


class TongueApp(MDApp):
    has_image_preview = BooleanProperty(False)
    _assistant_label = None
    _assistant_row = None
    _at_bottom = True
    _msg_meta = None  # label -> (bubble, thumb_h, bubble_padding, text_area_width)

    def _snack(self, text: str):
        # KivyMD 1.2.0：Snackbar(text=...) 已被弃用，正确做法是传入 MDLabel。
        MDSnackbar(MDLabel(text=str(text))).open()

    def _resolve_cjk_font(self):
        candidates = [
            Path("assets/fonts/NotoSansSC-Regular.ttc"),
            Path("assets/fonts/NotoSansSC-Regular.otf"),
            Path("assets/fonts/NotoSansSC-Regular.ttf"),
            Path("assets/fonts/SourceHanSansCN-Regular.otf"),
            Path("assets/fonts/SimHei.ttf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("/system/fonts/NotoSansCJK-Regular.ttc"),
            Path("/system/fonts/DroidSansFallback.ttf"),
        ]
        for font_path in candidates:
            if font_path.exists():
                return str(font_path)
        return ""

    def _setup_cjk_font(self):
        cjk_font = self._resolve_cjk_font()
        if not cjk_font:
            return
        # 全局覆盖 Kivy/KivyMD 默认文本字体，修复中文显示为方块/乱码。
        try:
            LabelBase.register(name="Roboto", fn_regular=cjk_font)
            LabelBase.register(name="RobotoThin", fn_regular=cjk_font)
            LabelBase.register(name="RobotoLight", fn_regular=cjk_font)
            LabelBase.register(name="RobotoMedium", fn_regular=cjk_font)
        except Exception:
            # 字体注册失败时不应阻塞应用启动（尤其是 Android 机型差异）。
            pass

    def build(self):
        # 桌面端：大屏但不全屏；Android 端由系统/打包参数控制。
        if platform != "android":
            try:
                Window.fullscreen = False
            except Exception:
                pass
            try:
                Window.maximize()
            except Exception:
                pass
            try:
                Window.bind(on_mouse_scroll=self._on_mouse_scroll)
            except Exception:
                pass
        self._setup_cjk_font()
        self.title = "舌征智析"
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "BlueGray"
        self.selected_image_path = ""
        self.dialog = None

        self.config_data = self._load_config()
        db_path = self._resolve_db_path(self.config_data.get("db_path", "tongue_records.db"))
        self.storage = LocalStorage(db_path)
        self.api_client = LLMApiClient(
            base_url=self.config_data.get("api_base_url", "http://8.160.184.31:8001"),
            api_path=self.config_data.get("api_path", "/v1/tongue-analyze"),
            text_api_path=self.config_data.get("text_api_path", "/v1/text-chat"),
            timeout=self.config_data.get("api_timeout", 90),
        )

        return Builder.load_file(KV_FILE)

    def _get_chat_scroll(self):
        if not self.root:
            return None
        return self.root.ids.get("chat_scroll")

    def _get_scroll_y(self):
        chat_scroll = self._get_chat_scroll()
        if not chat_scroll:
            return None
        try:
            return float(chat_scroll.scroll_y)
        except Exception:
            return None

    def _restore_scroll_y(self, prev_scroll_y):
        if prev_scroll_y is None:
            return
        chat_scroll = self._get_chat_scroll()
        if not chat_scroll:
            return
        Clock.schedule_once(lambda *_: setattr(chat_scroll, "scroll_y", prev_scroll_y), 0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._msg_meta = {}
        self._camera_request_code = 4721
        self._android_camera_bound = False

    def _on_mouse_scroll(self, _window, _x, _y, _scroll_x, scroll_y):
        # 桌面端：兜底实现鼠标滚轮上下滚动查看（避免某些环境 ScrollView 不响应）。
        try:
            chat_scroll = self.root.ids.get("chat_scroll") if self.root else None
            if not chat_scroll:
                return False
            if scroll_y == 0:
                return False
            # Kivy 的 scroll_y 范围通常是 0..1，1 表示顶部。
            # wheel up/down 的符号在不同平台可能相反，这里做一个经验方向修正。
            delta = -scroll_y * 0.08
            new_val = max(0.0, min(1.0, float(chat_scroll.scroll_y) + delta))
            chat_scroll.scroll_y = new_val
            return True
        except Exception:
            return False

    def on_start(self):
        if platform == "android" and request_permissions and Permission:
            # Android 各版本权限常量并不完全一致，动态探测后再请求，避免启动闪退。
            permission_names = [
                "CAMERA",
                "READ_MEDIA_IMAGES",      # Android 13+
                "READ_EXTERNAL_STORAGE",  # Android 12 及以下
            ]
            runtime_perms = []
            for perm_name in permission_names:
                perm_value = getattr(Permission, perm_name, None)
                if perm_value and perm_value not in runtime_perms:
                    runtime_perms.append(perm_value)
            try:
                if runtime_perms:
                    request_permissions(runtime_perms)
            except Exception:
                # 权限请求失败不影响主界面启动，避免“打开几秒后闪退”。
                pass
        # 不自动加载历史到聊天栏：避免“未发送就会滚动”的初始化抖动问题。
        # 监听输入内容变化：允许“仅文本咨询”。
        if "note_input" in self.root.ids:
            self.root.ids.note_input.bind(text=lambda *_: self._update_analyze_button())
        self._update_analyze_button()
        # 取消自动滚动：用户手动滚动后不再被程序强制拉回底部。
        if platform == "android":
            self._bind_android_activity_result()
            # 首次进入即显式请求拍照/选图所需权限，避免点击拍照才提示未授权。
            self._request_initial_android_permissions()

    def _request_initial_android_permissions(self):
        if platform != "android" or request_permissions is None:
            return
        perms = self._required_android_perms_for_capture()
        if not perms:
            return
        missing = [p for p in perms if not self._has_android_perm(p)]
        if not missing:
            return
        try:
            request_permissions(missing)
        except Exception:
            pass

    def _bind_android_activity_result(self):
        if self._android_camera_bound or android_activity is None:
            return
        try:
            android_activity.bind(on_activity_result=self._on_android_activity_result)
            self._android_camera_bound = True
        except Exception:
            self._android_camera_bound = False

    def _update_analyze_button(self):
        has_image = bool(self.selected_image_path)
        question = ""
        if "note_input" in self.root.ids:
            question = self.root.ids.note_input.text.strip()
        can_submit = has_image or bool(question)
        self.root.ids.analyze_btn.disabled = not can_submit

    def _on_chat_scroll(self, _instance, value):
        # 保留接口但不再使用（取消自动滚动后不会影响体验）
        return

    def _scroll_chat_to_bottom(self):
        # 自动滚动已取消：不再主动修改 scroll_y。
        return

    def _append_chat_message(self, role: str, text: str, image_path: str = ""):
        chat_list = self.root.ids.get("chat_list")
        if not chat_list:
            return None

        # 注意：不做任何 scroll_y 相关的程序控制，避免“未发送就滚动”。

        # DeepSeek 气泡底板取消：只保留“对齐+排版”，不绘制背景底板。
        bubble_padding = dp(12)
        # 动态计算气泡宽度，确保文本有足够空间显示
        chat_width = self.root.width if self.root else Window.width
        bubble_width = max(chat_width * 0.76, 200)  # 最小宽度200dp
        bubble = MDBoxLayout(
            orientation="vertical",
            padding=bubble_padding,
            size_hint_x=None,
            size_hint_y=None,
            width=bubble_width,
        )

        # 添加气泡背景
        from kivy.graphics import Color, RoundedRectangle
        bubble_color = (0.2, 0.6, 1, 0.9) if role == "user" else (0.15, 0.15, 0.17, 0.95)
        bubble_radius = [dp(16), dp(16), dp(16), dp(16)]

        # 为每个气泡创建独立的背景
        with bubble.canvas.before:
            Color(*bubble_color)
            bubble_rect = RoundedRectangle(
                pos=bubble.pos,
                size=bubble.size,
                radius=bubble_radius
            )

        # 更新气泡位置和大小的回调
        def update_bubble(instance, value):
            bubble_rect.pos = instance.pos
            bubble_rect.size = instance.size

        bubble.bind(pos=update_bubble, size=update_bubble)

        thumb_widget = None
        if role == "user" and image_path:
            thumb = MDBoxLayout(
                orientation="vertical",
                padding=0,
                size_hint_y=None,
                height=dp(140),
            )
            thumb.add_widget(
                AsyncImage(
                    source=image_path,
                    allow_stretch=True,
                    keep_ratio=True,
                )
            )
            thumb_widget = thumb
            bubble.add_widget(thumb)

        label = MDLabel(
            text=text,
            font_name="Roboto",
            adaptive_height=False,
            size_hint_y=None,
            size_hint_x=None,
            theme_text_color="Custom",
            text_color=(0.95, 0.98, 1, 1) if role == "user" else (1, 1, 1, 1),
            font_size="16sp",
            valign="top",
            halign="left",
        )
        # 关键：用“确定性高度计算”贴合文本长度，避免气泡底板过长。
        text_area_width = max(100.0, float(bubble.width) - float(bubble_padding) * 2)
        label.width = text_area_width
        # 给一个足够大的高度，确保 Label 能按 text_size 的宽度正确换行。
        label.text_size = (text_area_width, None)
        bubble.add_widget(label)

        # 强制更新文本布局并计算高度
        def update_bubble_height(*args):
            try:
                # 强制更新纹理
                label.texture_update()
                # 获取实际文本高度
                label_height = float(label.texture_size[1]) if label.texture_size else 0
                # 确保高度不为0
                if label_height <= 0:
                    label_height = 100  # 设置默认最小高度
                # 设置标签高度
                label.height = label_height
                # 计算气泡高度
                thumb_h = float(getattr(thumb_widget, "height", 0) or 0)
                bubble.height = thumb_h + label_height + float(bubble_padding) * 2
                # 更新行高度
                row.height = bubble.height
            except Exception:
                pass

        # 多次延迟更新，确保文本完全渲染
        Clock.schedule_once(update_bubble_height, 0)
        Clock.schedule_once(update_bubble_height, 0.1)
        Clock.schedule_once(update_bubble_height, 0.2)
        Clock.schedule_once(update_bubble_height, 0.3)
        Clock.schedule_once(update_bubble_height, 0.5)

        # 外层一行容器，用 Spacer 实现左右对齐。
        # 关键：固定 row 的高度，保证气泡更新后不会覆盖/顶出。
        row = MDBoxLayout(
            size_hint_x=1,
            size_hint_y=None,
            height=float(getattr(bubble, "height", 0) or 0),
            padding=0,
            spacing=0,
        )
        if role == "user":
            row.add_widget(Widget(size_hint_x=0.10))
            row.add_widget(bubble)
        else:
            row.add_widget(bubble)
            row.add_widget(Widget(size_hint_x=0.10))

        chat_list.add_widget(row)

        # 自动滚动到底部，确保新消息可见
        chat_scroll = self.root.ids.get("chat_scroll")
        if chat_scroll:
            Clock.schedule_once(lambda dt: setattr(chat_scroll, 'scroll_y', 0), 0.1)

        if role == "assistant":
            self._assistant_label = label
            self._assistant_row = row
        # 缓存这个消息的布局参数，便于“回复更新时”重新计算高度。
        try:
            self._msg_meta[label] = (
                bubble,
                row,
                float(getattr(thumb_widget, "height", 0) or 0),
                bubble_padding,
                text_area_width,
            )
        except Exception:
            pass

        return label

    def _refresh_message_height(self, label, new_text: str):
        meta = self._msg_meta.get(label)
        if not meta:
            return
        bubble, row, thumb_h, bubble_padding, text_area_width = meta

        # 更新文本内容
        label.text = new_text
        label.width = text_area_width
        label.text_size = (text_area_width, None)

        # 强制更新文本布局并计算高度
        def update_bubble_height(*args):
            try:
                # 强制更新纹理
                label.texture_update()
                # 获取实际文本高度
                label_height = float(label.texture_size[1]) if label.texture_size else 0
                # 设置标签高度
                label.height = label_height
                # 计算气泡高度
                bubble.height = thumb_h + label_height + float(bubble_padding) * 2
                # 更新行高度
                row.height = bubble.height
            except Exception:
                pass

        # 多次延迟更新，确保文本完全渲染
        Clock.schedule_once(update_bubble_height, 0)
        Clock.schedule_once(update_bubble_height, 0.1)
        Clock.schedule_once(update_bubble_height, 0.2)

        # 保持滚动位置，避免滚动跳动

    def _render_recent_chat(self):
        chat_list = self.root.ids.get("chat_list")
        if not chat_list:
            return
        chat_list.clear_widgets()
        self._assistant_label = None
        records = self.storage.list_records(limit=20)
        # records 按 id desc 返回，反过来更接近聊天顺序。
        for rec in reversed(records):
            img = (rec.get("image_path") or "").strip()
            if img:
                self._append_chat_message("user", "舌象图片（已发送）", image_path=img)
                self._append_chat_message("assistant", rec.get("full_result") or "")
            else:
                self._append_chat_message("user", "仅文本咨询")
                self._append_chat_message("assistant", rec.get("full_result") or "")

    def _load_config(self):
        default = {
            "api_base_url": "http://8.160.184.31:8001",
            "api_path": "/v1/tongue-analyze",
            "text_api_path": "/v1/text-chat",
            "api_timeout": 90,
            "api_token": "",
            "retry_count": 2,
            "retry_backoff_sec": 1.5,
            "db_path": "tongue_records.db",
        }

        runtime_config_path = self._resolve_runtime_config_path()
        bundled_config_path = Path(CONFIG_FILE)

        # 优先读取可写目录内配置（Android 上避免读取 APK 内只读文件）。
        if runtime_config_path.exists():
            try:
                return json.loads(runtime_config_path.read_text(encoding="utf-8"))
            except Exception:
                return default

        # 首次启动：尝试从项目配置文件复制；失败则使用默认配置。
        config_data = default
        if bundled_config_path.exists():
            try:
                config_data = json.loads(bundled_config_path.read_text(encoding="utf-8"))
            except Exception:
                config_data = default

        try:
            runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_config_path.write_text(
                json.dumps(config_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            # 写入失败时仍返回可用配置，避免启动崩溃。
            pass
        return config_data

    def _resolve_runtime_config_path(self) -> Path:
        # Android 下统一放在 user_data_dir；桌面端保持项目目录行为。
        if platform == "android":
            return Path(self.user_data_dir) / CONFIG_FILE
        return Path(CONFIG_FILE)

    def _resolve_db_path(self, db_path: str) -> str:
        p = Path(db_path)
        if p.is_absolute():
            return str(p)
        # Android 下相对路径映射到可写目录，避免 sqlite 在只读目录创建失败。
        if platform == "android":
            return str(Path(self.user_data_dir) / p.name)
        return str(p)

    def pick_image(self):
        # Android：直接拉起系统相册，用户选择后自动回填。
        if platform == "android" and filechooser is not None:
            try:
                filechooser.open_file(
                    on_selection=self._on_android_pick_image,
                    multiple=False,
                    filters=["*.jpg", "*.jpeg", "*.png", "*.webp"],
                    use_extensions=True,
                )
                return
            except Exception:
                self._snack("打开系统相册失败，请重试")
                return

        # 桌面端保留文件选择弹窗。
        chooser = FileChooserListView(
            path=os.path.expanduser("~"),
            filters=["*.jpg", "*.jpeg", "*.png", "*.webp"],
            multiselect=False,
            dirselect=False,
        )
        self.dialog = MDDialog(
            title="选择舌象图片",
            type="custom",
            content_cls=chooser,
            auto_dismiss=False,
            buttons=[
                MDTextButton(text="取消", on_release=lambda *_: self.dialog.dismiss()),
                MDRaisedButton(text="确定", on_release=partial(self._confirm_pick_image, chooser)),
            ],
        )
        # 用户选中后，立刻回填预览，避免“点确定但选中未生效”的体验问题。
        def _on_sel(*_):
            if chooser.selection:
                self.selected_image_path = chooser.selection[0]
                # 取消顶部图片预览：图片以聊天气泡缩略图形式展示
                self.has_image_preview = True
                self._update_analyze_button()

        chooser.bind(selection=_on_sel)
        self.dialog.open()

    def _on_android_pick_image(self, selection):
        # plyer 回调可能在非主线程，UI 更新回到主线程执行。
        Clock.schedule_once(lambda *_: self._apply_android_pick_result(selection), 0)

    def _apply_android_pick_result(self, selection):
        if not selection:
            self._snack("未选择图片")
            return
        try:
            picked = str(selection[0])
        except Exception:
            self._snack("选择图片失败")
            return
        local_path = self._ensure_local_image_path(picked)
        if not local_path:
            self._snack("图片读取失败，请换一张图片重试")
            return
        self.selected_image_path = local_path
        self.has_image_preview = True
        self._update_analyze_button()
        self._snack("图片加载成功")

    def _ensure_local_image_path(self, source_path: str) -> str:
        if not source_path:
            return ""
        if platform != "android":
            return source_path.replace("file://", "")

        # Android 下统一拷贝到 app 私有目录，规避临时 URI/临时文件失效导致 Errno2。
        target_dir = Path(self.user_data_dir) / "picked"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = str(target_dir / f"pick_{int(time.time() * 1000)}.jpg")

        # file:// 或绝对路径：直接复制
        raw = source_path
        if raw.startswith("file://"):
            raw = unquote(raw.replace("file://", ""))
        if raw.startswith("/") and os.path.exists(raw):
            try:
                ext = Path(raw).suffix.lower() or ".jpg"
                final_target = str(target_dir / f"pick_{int(time.time() * 1000)}{ext}")
                shutil.copy2(raw, final_target)
                return final_target
            except Exception:
                return ""

        # 仅 Android 处理 content:// URI
        if not source_path.startswith("content://") or autoclass is None:
            # 兜底：如果路径存在则返回，否则失败
            return source_path if os.path.exists(source_path) else ""
        try:
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Uri = autoclass("android.net.Uri")
            FileOutputStream = autoclass("java.io.FileOutputStream")
            resolver = PythonActivity.mActivity.getContentResolver()
            uri = Uri.parse(source_path)
            mime = resolver.getType(uri) or ""
            suffix = ".jpg"
            if "png" in mime:
                suffix = ".png"
            elif "webp" in mime:
                suffix = ".webp"
            target = str(target_dir / f"pick_{int(time.time() * 1000)}{suffix}")
            ins = resolver.openInputStream(uri)
            if ins is None:
                return ""
            outs = FileOutputStream(target)
            try:
                while True:
                    b = ins.read()
                    if b == -1:
                        break
                    outs.write(b)
                outs.flush()
            finally:
                try:
                    ins.close()
                except Exception:
                    pass
                try:
                    outs.close()
                except Exception:
                    pass
            return target if os.path.exists(target) else ""
        except Exception:
            return ""

    def _required_android_perms_for_capture(self):
        if not Permission:
            return []
        names = [
            "CAMERA",
            "READ_MEDIA_IMAGES",      # Android 13+
            "READ_EXTERNAL_STORAGE",  # Android 12 及以下
            "WRITE_EXTERNAL_STORAGE",
        ]
        perms = []
        for n in names:
            v = getattr(Permission, n, None)
            if v and v not in perms:
                perms.append(v)
        return perms

    def _has_android_perm(self, perm):
        if not perm:
            return True
        if check_permission is None:
            return True
        try:
            return bool(check_permission(perm))
        except Exception:
            return True

    def _request_capture_permissions_then(self, on_granted):
        perms = self._required_android_perms_for_capture()
        if not perms or request_permissions is None:
            on_granted()
            return
        missing = [p for p in perms if not self._has_android_perm(p)]
        if not missing:
            on_granted()
            return

        def _cb(_permissions, grants):
            ok = True
            try:
                ok = all(bool(x) for x in grants)
            except Exception:
                ok = False
            if ok:
                Clock.schedule_once(lambda *_: on_granted(), 0)
            else:
                Clock.schedule_once(lambda *_: self._snack("未授予拍照所需权限"), 0)

        try:
            request_permissions(missing, _cb)
        except Exception:
            self._snack("权限请求失败，请到系统设置开启相机权限")

    def capture_image(self):
        if platform != "android":
            self._snack("拍照功能需在 Android 真机使用")
            return
        self._request_capture_permissions_then(self._do_capture_image)

    def _do_capture_image(self):
        # Android 优先走系统相机 Intent，兼容性高于 plyer.camera。
        if self._start_android_camera_intent():
            self._set_loading(True, "正在打开系统相机...")
            return
        # 回退到 plyer 拍照方案
        if camera is None:
            self._snack("未检测到拍照组件，请确认已安装 plyer")
            return
        # Android 上写入目录使用 user_data_dir，保证可写且跨设备可用。
        capture_dir = Path(self.user_data_dir) / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("tongue_%Y%m%d_%H%M%S.jpg")
        target = str(capture_dir / filename)
        self._set_loading(True, "正在打开相机，请拍摄舌象...")
        try:
            camera.take_picture(filename=target, on_complete=self._on_camera_complete)
        except Exception:
            self._set_loading(False)
            self._snack("拍照启动失败，请检查系统相机权限")

    def _start_android_camera_intent(self) -> bool:
        if platform != "android" or autoclass is None:
            return False
        try:
            self._bind_android_activity_result()
            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            act = PythonActivity.mActivity
            if intent.resolveActivity(act.getPackageManager()) is None:
                return False
            act.startActivityForResult(intent, self._camera_request_code)
            return True
        except Exception:
            return False

    def _on_android_activity_result(self, request_code, result_code, intent):
        if int(request_code) != int(self._camera_request_code):
            return
        Clock.schedule_once(lambda *_: self._apply_android_camera_result(int(result_code), intent), 0)

    def _apply_android_camera_result(self, result_code: int, intent):
        self._set_loading(False)
        try:
            Activity = autoclass("android.app.Activity")
            ok = int(result_code) == int(Activity.RESULT_OK)
        except Exception:
            ok = False
        if not ok or intent is None:
            self._snack("拍照取消或失败")
            return
        path = self._save_camera_thumb_from_intent(intent)
        if not path:
            self._snack("拍照结果读取失败，请改用选图")
            return
        self.selected_image_path = path
        self.has_image_preview = True
        self._update_analyze_button()
        self._snack("拍照成功，已加载图片")

    def _save_camera_thumb_from_intent(self, intent) -> str:
        if platform != "android" or autoclass is None or intent is None:
            return ""
        try:
            Bitmap = autoclass("android.graphics.Bitmap")
            FileOutputStream = autoclass("java.io.FileOutputStream")
            extras = intent.getExtras()
            if extras is None:
                return ""
            bmp = extras.get("data")
            if bmp is None:
                return ""
            capture_dir = Path(self.user_data_dir) / "captures"
            capture_dir.mkdir(parents=True, exist_ok=True)
            target = str(capture_dir / f"cam_{int(time.time() * 1000)}.jpg")
            outs = FileOutputStream(target)
            try:
                bmp.compress(Bitmap.CompressFormat.JPEG, 95, outs)
                outs.flush()
            finally:
                try:
                    outs.close()
                except Exception:
                    pass
            return target
        except Exception:
            return ""

    def _on_camera_complete(self, filepath):
        Clock.schedule_once(lambda *_: self._apply_camera_result(filepath), 0)

    def _apply_camera_result(self, filepath):
        self._set_loading(False)
        if not filepath:
            self._snack("拍照取消或失败")
            return
        self.selected_image_path = filepath
        self.has_image_preview = True
        self._update_analyze_button()
        self._snack("拍照成功，已加载图片")

    def _confirm_pick_image(self, chooser, *_):
        if chooser.selection:
            self.selected_image_path = chooser.selection[0]
            self.has_image_preview = True
            self._update_analyze_button()
            self._snack("图片加载成功")
        else:
            self._snack("未选择图片，请先在列表中点一下图片")
        if self.dialog:
            self.dialog.dismiss()

    def analyze_now(self):
        user_text = self.root.ids.note_input.text.strip()
        has_image = bool(self.selected_image_path)
        if has_image:
            # 发送前再次确保图片是可读本地文件，避免 content/临时路径导致 Errno2。
            repaired = self._ensure_local_image_path(self.selected_image_path)
            if not repaired or not os.path.exists(repaired):
                self._snack("图片路径失效，请重新选择图片后再发送")
                return
            self.selected_image_path = repaired

        if not has_image and not user_text:
            self._snack("请输入问题，或先选择/拍摄舌象图片")
            return

        # 发消息：先把用户输入展示出来。
        if has_image:
            user_msg = user_text if user_text else "（仅舌象图片）"
            self._append_chat_message("user", user_msg, image_path=self.selected_image_path)
        else:
            self._append_chat_message("user", user_text)

        # 清空输入框，模拟 DeepSeek 的聊天体验。
        self.root.ids.note_input.text = ""
        self._update_analyze_button()

        # 助手占位气泡
        prev_scroll_y = self._get_scroll_y()
        self._append_chat_message("assistant", "正在思考，请稍候...")
        self._restore_scroll_y(prev_scroll_y)
        # 取消自动滚动：由用户手动滚轮查看。

        # 禁用发送按钮，等待线程回填。
        self._set_loading(True)
        threading.Thread(
            target=self._analyze_worker,
            args=("image" if has_image else "text", self.selected_image_path or "", user_text),
            daemon=True,
        ).start()

    def _analyze_worker(self, mode: str, image_path: str, user_note: str):
        headers = {}
        token = self.config_data.get("api_token", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        retry_count = int(self.config_data.get("retry_count", 2))
        backoff = float(self.config_data.get("retry_backoff_sec", 1.5))
        attempts = retry_count + 1
        last_error = ""

        for idx in range(1, attempts + 1):
            try:
                if mode == "image":
                    data = self.api_client.analyze_tongue_image(
                        image_path=image_path,
                        user_note=user_note,
                        extra_headers=headers,
                    )
                else:
                    data = self.api_client.text_chat(question=user_note, extra_headers=headers)

                Clock.schedule_once(
                    lambda *_: self._on_analyze_success(data, image_path, mode),
                    0,
                )
                return
            except Exception as e:
                last_error = str(e)
                if idx < attempts:
                    wait_sec = backoff * idx
                    Clock.schedule_once(
                        lambda *_ , n=idx, t=wait_sec: self._set_loading(
                            True, f"网络波动，准备第 {n + 1} 次重试（{t:.1f}s）..."
                        ),
                        0,
                    )
                    time.sleep(wait_sec)

        Clock.schedule_once(lambda *_: self._on_analyze_failed(last_error, mode), 0)

    def _on_analyze_success(self, data, image_path, mode: str):
        self._set_loading(False)
        answer = str(data.get("answer", "")).strip()
        brief = answer[:40] if answer else "分析完成"
        full = answer if answer else str(data)
        model_name = str(data.get("model", "ShizhenGPT-7B-VL"))
        confidence = data.get("confidence")

        prev_scroll_y = self._get_scroll_y()

        self.storage.add_record(
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            image_path=image_path or "",
            brief_result=brief,
            full_result=full,
            model_name=model_name if mode == "image" else "TextChat",
            confidence=confidence,
        )
        # 刷新占位文本，不删除占位行，避免结构变化导致的跳动/灰底。
        if getattr(self, "_assistant_label", None) is not None:
            self._refresh_message_height(self._assistant_label, full)
        self._restore_scroll_y(prev_scroll_y)

    def _on_analyze_failed(self, msg, mode: str = "image"):
        self._set_loading(False)
        lower = msg.lower()
        if "timed out" in lower or "timeout" in lower:
            friendly = "请求超时：模型处理时间较长或网络不稳定，请稍后重试。"
        elif "401" in lower or "403" in lower:
            friendly = "鉴权失败：请检查 app_config.json 的 api_token 是否正确。"
        elif "404" in lower:
            friendly = "接口不存在：请检查 api_base_url 与 api_path 配置。"
        else:
            friendly = "服务调用失败：请检查服务器状态和网络连接。"
        self._snack("调用失败")
        prev_scroll_y = self._get_scroll_y()
        if getattr(self, "_assistant_label", None) is not None:
            self._refresh_message_height(self._assistant_label, f"{friendly}\n\n原始错误: {msg}")
        self._restore_scroll_y(prev_scroll_y)

    def _set_loading(self, is_loading: bool, text: str = ""):
        self.root.ids.analyze_btn.disabled = True if is_loading else False
        self.root.ids.progress_bar.opacity = 1 if is_loading else 0
        self.root.ids.progress_bar.active = is_loading
        if not is_loading:
            self._update_analyze_button()

    def _render_history(self):
        container = self.root.ids.history_list
        container.clear_widgets()
        records = self.storage.list_records(limit=30)
        for rec in records:
            left_icon = IconLeftWidget(icon="history")
            img = (rec.get("image_path") or "").strip()
            img_text = img if img else "仅文本咨询"
            item = HistoryItem(
                text=f"{rec['created_at']}  {rec['brief_result'][:26]}",
                secondary_text=f"{rec['model_name']}  {img_text}",
                on_release=partial(self._show_record_detail, rec),
            )
            item.add_widget(left_icon)
            container.add_widget(item)

    def _show_record_detail(self, rec, *_):
        img = (rec.get("image_path") or "").strip()
        msg = (
            f"时间: {rec['created_at']}\n\n"
            + (f"图片: {img}\n\n" if img else "图片:（仅文本）\n\n")
            + f"模型: {rec['model_name']}\n\n"
            + f"结论:\n{rec['full_result']}"
        )
        self.dialog = MDDialog(
            title="历史分析详情",
            text=msg,
            buttons=[MDRaisedButton(text="关闭", on_release=lambda *_: self.dialog.dismiss())],
        )
        self.dialog.open()


if __name__ == "__main__":
    TongueApp().run()

