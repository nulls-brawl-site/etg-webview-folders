import hashlib
import json
import os
import time
import urllib.request

from android_utils import run_on_ui_thread
from base_plugin import AppEvent, BasePlugin, MethodHook
from client_utils import PLUGINS_QUEUE, get_last_fragment, run_on_queue
from file_utils import ensure_dir_exists, get_plugins_dir
from java import jclass
from java.lang import ClassLoader
from ui.settings import Divider, Header, Input, Text

__id__ = "etg_webview_folders"
__name__ = "WebView Folders"
__description__ = "Adds configurable Telegram folder tabs which open websites in a sandboxed WebView."
__author__ = "@bsod4ik_plugins"
__version__ = "1.0.0"
__icon__ = "msg_language"
__app_version__ = ">=12.5.1"
__sdk_version__ = ">=1.4.3.3"

ENTRY_CLASS = "com.etgwebfolders.bridge.WebFoldersBridge"
DEFAULT_DEX_URL = "https://raw.githubusercontent.com/nulls-brawl-site/etg-webview-folders/master/build/etg-webview-folders-bridge.dex"
DEFAULT_DEX_SHA256 = "4ddc5313023915b6dc2e16d33689b25d897e9f543a9bd428a88a89a96f99e51f"
MAIN_PREFS_ITEM_ID = 0x575646
CONFIG_KEY = "webview_folders_config"


class _AfterCreateView(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        self.plugin.install_tabs(param.thisObject)


class _BeforeUpdateTabs(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        self.plugin.before_update_tabs(param.thisObject)


class _AfterUpdateTabs(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        self.plugin.install_tabs(param.thisObject)


class _BeforeDestroy(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        self.plugin.hide_tabs(param.thisObject)


class _AfterMainPrefsFill(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        self.plugin.inject_main_preferences_item(param)


class _BeforeMainPrefsClick(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        if self.plugin.handle_main_preferences_click(param):
            param.setResult(None)


class WebViewFoldersPlugin(BasePlugin):
    def on_plugin_load(self):
        self._bridge = None
        self._bridge_install = None
        self._bridge_before_update = None
        self._bridge_hide = None
        self._bridge_configure = None
        self._bridge_snapshot = None
        self._bridge_ready = False
        self._pending_fragments = []
        self._last_snapshot = {"tabs": []}
        self._install_hooks()
        run_on_queue(self._load_bridge, PLUGINS_QUEUE)

    def on_plugin_unload(self):
        self._pending_fragments = []

    def on_app_event(self, event_type: AppEvent):
        if event_type == AppEvent.RESUME:
            self._schedule_current_fragment_install()

    def create_settings(self):
        config = self._config()
        rows = [
            Header(text="WebView папки"),
            Text(
                text="Добавить папку",
                subtext="Название, сайт и иконка",
                icon="msg_add",
                accent=True,
                create_sub_fragment=lambda: self._create_add_settings(),
            ),
        ]
        tabs = config.get("tabs", [])
        if tabs:
            rows.append(Header(text="Папки"))
            for tab in tabs:
                rows.append(Text(
                    text=tab.get("title") or self._host_from_url(tab.get("url", "")),
                    subtext=tab.get("url", ""),
                    icon="msg_language",
                    create_sub_fragment=lambda tab_key=tab.get("key"): self._create_tab_settings(tab_key),
                ))
        else:
            rows.append(Text(
                text="Папок нет",
                subtext="Создай первую WebView папку",
                icon="msg_info",
            ))
        rows.extend([
            Divider(),
            Header(text="Порядок вкладок"),
            Text(
                text="Настроить порядок",
                subtext="Можно двигать web-папки и родные папки Telegram",
                icon="msg_list",
                create_sub_fragment=lambda: self._create_order_settings(),
            ),
        ])
        return rows

    def _create_add_settings(self):
        return [
            Header(text="Новая папка"),
            Input("draft_title", "Название", default=self.get_setting("draft_title", ""), icon="msg_edit"),
            Input("draft_url", "Сайт", default=self.get_setting("draft_url", ""), icon="msg_language"),
            Input("draft_emoticon", "Иконка", default=self.get_setting("draft_emoticon", "🌐"), icon="msg_emoji"),
            Divider(),
            Text(text="Создать", icon="msg_add", accent=True, on_click=lambda _v: self._add_tab_from_draft()),
        ]

    def _create_tab_settings(self, tab_key):
        tab = self._find_tab(tab_key)
        if not tab:
            return [Text(text="Папка не найдена", icon="mtrl_ic_error", red=True)]
        return [
            Header(text=tab.get("title") or "WebView"),
            Input(
                f"tab_{tab_key}_title",
                "Название",
                default=tab.get("title", ""),
                icon="msg_edit",
                on_change=lambda value, key=tab_key: self._update_tab(key, "title", value),
            ),
            Input(
                f"tab_{tab_key}_url",
                "Сайт",
                default=tab.get("url", ""),
                icon="msg_language",
                on_change=lambda value, key=tab_key: self._update_tab(key, "url", value),
            ),
            Input(
                f"tab_{tab_key}_emoticon",
                "Иконка",
                default=tab.get("emoticon", "🌐"),
                icon="msg_emoji",
                on_change=lambda value, key=tab_key: self._update_tab(key, "emoticon", value or "🌐"),
            ),
            Divider(),
            Text(text="Левее", icon="material_ic_keyboard_arrow_left_black_24dp", on_click=lambda _v, key=tab_key: self._move_token(f"web:{key}", -1)),
            Text(text="Правее", icon="material_ic_keyboard_arrow_right_black_24dp", on_click=lambda _v, key=tab_key: self._move_token(f"web:{key}", 1)),
            Divider(),
            Text(text="Удалить", icon="msg_delete", red=True, on_click=lambda _v, key=tab_key: self._delete_tab(key)),
        ]

    def _create_order_settings(self):
        snapshot = self._snapshot()
        tabs = snapshot.get("tabs", [])
        if not tabs:
            return [
                Header(text="Порядок"),
                Text(text="Открой список чатов", subtext="После этого здесь появятся родные папки Telegram", icon="msg_info"),
            ]
        rows = [Header(text="Порядок")]
        for item in tabs:
            token = item.get("token", "")
            title = item.get("title", token)
            prefix = "WebView" if item.get("web") else "Telegram"
            rows.append(Text(
                text=title,
                subtext=prefix,
                icon="msg_language" if item.get("web") else "msg_folder",
                create_sub_fragment=lambda item_token=token, item_title=title: [
                    Header(text=item_title),
                    Text(text="Левее", icon="material_ic_keyboard_arrow_left_black_24dp", on_click=lambda _v, t=item_token: self._move_token(t, -1)),
                    Text(text="Правее", icon="material_ic_keyboard_arrow_right_black_24dp", on_click=lambda _v, t=item_token: self._move_token(t, 1)),
                ],
            ))
        return rows

    def _install_hooks(self):
        DialogsActivity = self._class_ref("org.telegram.ui.DialogsActivity")
        MainTabsActivity = self._class_ref("org.telegram.ui.MainTabsActivity")
        MainPreferencesActivity = self._class_ref("com.exteragram.messenger.preferences.MainPreferencesActivity")
        Context = self._class_ref("android.content.Context")
        ArrayList = self._class_ref("java.util.ArrayList")
        UniversalAdapter = self._class_ref("org.telegram.ui.Components.UniversalAdapter")
        UItem = self._class_ref("org.telegram.ui.Components.UItem")
        View = self._class_ref("android.view.View")
        Boolean = jclass("java.lang.Boolean")
        Integer = jclass("java.lang.Integer")
        Float = jclass("java.lang.Float")
        try:
            if DialogsActivity is not None and Context is not None:
                create_view = DialogsActivity.getDeclaredMethod("createView", Context)
                create_view.setAccessible(True)
                self.hook_method(create_view, _AfterCreateView(self))

                update_tabs = DialogsActivity.getDeclaredMethod("updateFilterTabs", Boolean.TYPE, Boolean.TYPE)
                update_tabs.setAccessible(True)
                self.hook_method(update_tabs, _BeforeUpdateTabs(self))
                self.hook_method(update_tabs, _AfterUpdateTabs(self))

                destroy = DialogsActivity.getDeclaredMethod("onFragmentDestroy")
                destroy.setAccessible(True)
                self.hook_method(destroy, _BeforeDestroy(self))

            if MainTabsActivity is not None and Context is not None:
                main_create_view = MainTabsActivity.getDeclaredMethod("createView", Context)
                main_create_view.setAccessible(True)
                self.hook_method(main_create_view, _AfterCreateView(self))

                main_resume = MainTabsActivity.getDeclaredMethod("onResume")
                main_resume.setAccessible(True)
                self.hook_method(main_resume, _AfterCreateView(self))

                Bundle = self._class_ref("android.os.Bundle")
                if Bundle is not None:
                    prepare_dialogs = MainTabsActivity.getDeclaredMethod("prepareDialogsActivity", Bundle)
                    prepare_dialogs.setAccessible(True)
                    self.hook_method(prepare_dialogs, _AfterCreateView(self))

            if MainPreferencesActivity is not None and ArrayList is not None and UniversalAdapter is not None:
                fill_items = MainPreferencesActivity.getDeclaredMethod("fillItems", ArrayList, UniversalAdapter)
                fill_items.setAccessible(True)
                self.hook_method(fill_items, _AfterMainPrefsFill(self))

            if MainPreferencesActivity is not None and UItem is not None and View is not None:
                on_click = MainPreferencesActivity.getDeclaredMethod(
                    "onClick", UItem, View, Integer.TYPE, Float.TYPE, Float.TYPE
                )
                on_click.setAccessible(True)
                self.hook_method(on_click, _BeforeMainPrefsClick(self))
        except Exception:
            return

    def _load_bridge(self):
        try:
            dex_path = self._ensure_dex()
            if not dex_path:
                return
            ctx = jclass("org.telegram.messenger.ApplicationLoader").applicationContext
            opt_dir = os.path.join(self._dex_dir(), "dex_opt")
            ensure_dir_exists(opt_dir)
            DexClassLoader = jclass("dalvik.system.DexClassLoader")
            loader = DexClassLoader(dex_path, opt_dir, None, ctx.getClassLoader() or ClassLoader.getSystemClassLoader())
            self._bridge = loader.loadClass(ENTRY_CLASS)
            Object = self._class_ref("java.lang.Object")
            String = self._class_ref("java.lang.String")
            self._bridge_install = self._bridge.getDeclaredMethod("installWithStatus", Object)
            self._bridge_install.setAccessible(True)
            self._bridge_before_update = self._bridge.getDeclaredMethod("beforeUpdateFilterTabs", Object)
            self._bridge_before_update.setAccessible(True)
            self._bridge_hide = self._bridge.getDeclaredMethod("hide", Object)
            self._bridge_hide.setAccessible(True)
            self._bridge_configure = self._bridge.getDeclaredMethod("configure", String)
            self._bridge_configure.setAccessible(True)
            self._bridge_snapshot = self._bridge.getDeclaredMethod("getTabsSnapshotJson")
            self._bridge_snapshot.setAccessible(True)
            self._bridge_ready = True
            self._sync_bridge_config(False)
            for fragment in list(self._pending_fragments):
                self.install_tabs(fragment)
            self._pending_fragments = []
            self._schedule_current_fragment_install()
        except Exception:
            self._bridge_ready = False

    def install_tabs(self, fragment):
        if fragment is None:
            return
        if not self._bridge_ready:
            if fragment not in self._pending_fragments:
                self._pending_fragments.append(fragment)
            return
        try:
            self._sync_bridge_config(False)
            self._bridge_install.invoke(None, fragment)
            self._pull_snapshot()
        except Exception:
            return

    def before_update_tabs(self, fragment):
        if self._bridge_ready and self._bridge_before_update is not None and fragment is not None:
            try:
                self._sync_bridge_config(False)
                self._bridge_before_update.invoke(None, fragment)
            except Exception:
                return

    def hide_tabs(self, fragment):
        if self._bridge_ready and self._bridge_hide is not None and fragment is not None:
            try:
                self._bridge_hide.invoke(None, fragment)
            except Exception:
                return

    def inject_main_preferences_item(self, param):
        try:
            items = param.args[0]
            if items is None or self._has_uitem_id(items, MAIN_PREFS_ITEM_ID):
                return
            UItem = self._class_ref("org.telegram.ui.Components.UItem")
            RDrawable = self._class_ref("org.telegram.messenger.R$drawable")
            Integer = jclass("java.lang.Integer")
            CharSequence = self._class_ref("java.lang.CharSequence")
            icon_field = RDrawable.getDeclaredField("msg_language")
            icon_field.setAccessible(True)
            icon_id = icon_field.getInt(None)
            as_button = UItem.getMethod("asButton", Integer.TYPE, Integer.TYPE, CharSequence)
            item = as_button.invoke(None, MAIN_PREFS_ITEM_ID, icon_id, "Настройки WebView папок")
            insert_at = self._find_insert_after_plugins(items)
            items.add(insert_at, item)
        except Exception:
            return

    def handle_main_preferences_click(self, param):
        try:
            item = param.args[0]
            if item is None or int(item.id) != MAIN_PREFS_ITEM_ID:
                return False
            plugin = self._plugin_model()
            if plugin is None:
                return False
            PluginSettingsActivity = self._class_ref("com.exteragram.messenger.plugins.ui.PluginSettingsActivity")
            Plugin = self._class_ref("com.exteragram.messenger.plugins.Plugin")
            BaseFragment = self._class_ref("org.telegram.ui.ActionBar.BaseFragment")
            ctor = PluginSettingsActivity.getConstructor(Plugin)
            settings = ctor.newInstance(plugin)
            present = BaseFragment.getMethod("presentFragment", BaseFragment)
            present.invoke(param.thisObject, settings)
            return True
        except Exception:
            return False

    def _ensure_dex(self):
        dex_dir = self._dex_dir()
        ensure_dir_exists(dex_dir)
        dex_path = os.path.join(dex_dir, "etg-webview-folders-bridge.dex")
        expected_sha = DEFAULT_DEX_SHA256
        if os.path.exists(dex_path) and os.path.getsize(dex_path) > 1024:
            if not expected_sha or self._sha256(dex_path) == expected_sha:
                self._make_read_only(dex_path)
                return dex_path
        tmp_path = dex_path + ".tmp"
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            with urllib.request.urlopen(DEFAULT_DEX_URL, timeout=25) as response:
                data = response.read()
            if len(data) < 1024:
                return None
            got_sha = hashlib.sha256(data).hexdigest()
            if expected_sha and got_sha != expected_sha:
                return None
            with open(tmp_path, "wb") as f:
                f.write(data)
            self._make_read_only(tmp_path)
            os.replace(tmp_path, dex_path)
            self._make_read_only(dex_path)
            return dex_path
        except Exception:
            return None
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                return None

    def _sync_bridge_config(self, reinstall=True):
        if not self._bridge_ready or self._bridge_configure is None:
            return
        try:
            self._bridge_configure.invoke(None, self._config_json())
            if reinstall:
                self._schedule_current_fragment_install()
        except Exception:
            return

    def _schedule_current_fragment_install(self):
        if not self._bridge_ready:
            return
        for delay in (0, 250, 1000):
            run_on_ui_thread(lambda: self._install_current_fragment(), delay)

    def _install_current_fragment(self):
        try:
            self.install_tabs(get_last_fragment())
        except Exception:
            return

    def _pull_snapshot(self):
        try:
            if self._bridge_snapshot is not None:
                raw = str(self._bridge_snapshot.invoke(None))
                self._last_snapshot = json.loads(raw or '{"tabs":[]}')
        except Exception:
            return

    def _snapshot(self):
        self._pull_snapshot()
        return self._last_snapshot if isinstance(self._last_snapshot, dict) else {"tabs": []}

    def _config_json(self):
        return json.dumps(self._config(), ensure_ascii=False, separators=(",", ":"))

    def _config(self):
        try:
            data = json.loads(self.get_setting(CONFIG_KEY, '{"tabs":[],"order":[]}') or "{}")
        except Exception:
            data = {}
        if not isinstance(data.get("tabs"), list):
            data["tabs"] = []
        if not isinstance(data.get("order"), list):
            data["order"] = []
        return data

    def _save_config(self, config, reload_settings=True):
        self.set_setting(CONFIG_KEY, json.dumps(config, ensure_ascii=False, separators=(",", ":")), reload_settings=reload_settings)
        self._sync_bridge_config(True)

    def _add_tab_from_draft(self):
        title = (self.get_setting("draft_title", "") or "").strip()
        url = self._normalize_url(self.get_setting("draft_url", "") or "")
        emoticon = (self.get_setting("draft_emoticon", "🌐") or "🌐").strip()[:8]
        if not url:
            return
        config = self._config()
        key = f"tab_{int(time.time() * 1000)}"
        config["tabs"].append({
            "key": key,
            "title": title or self._host_from_url(url),
            "url": url,
            "emoticon": emoticon or "🌐",
            "enabled": True,
        })
        config["order"].append(f"web:{key}")
        self.set_setting("draft_title", "", reload_settings=False)
        self.set_setting("draft_url", "", reload_settings=False)
        self._save_config(config)

    def _update_tab(self, key, field, value):
        config = self._config()
        for tab in config["tabs"]:
            if tab.get("key") == key:
                if field == "url":
                    normalized = self._normalize_url(value)
                    if not normalized:
                        return
                    value = normalized
                tab[field] = (value or "").strip()
                if field == "emoticon" and not tab[field]:
                    tab[field] = "🌐"
                break
        self._save_config(config, reload_settings=False)

    def _delete_tab(self, key):
        config = self._config()
        config["tabs"] = [tab for tab in config["tabs"] if tab.get("key") != key]
        config["order"] = [token for token in config["order"] if token != f"web:{key}"]
        self._save_config(config)

    def _move_token(self, token, direction):
        if not token:
            return
        config = self._config()
        order = self._full_order(config)
        if token not in order:
            order.append(token)
        index = order.index(token)
        new_index = max(0, min(len(order) - 1, index + direction))
        if index == new_index:
            return
        order[index], order[new_index] = order[new_index], order[index]
        config["order"] = order
        self._save_config(config)

    def _full_order(self, config):
        snapshot_tokens = [item.get("token") for item in self._snapshot().get("tabs", []) if item.get("token")]
        web_tokens = [f"web:{tab.get('key')}" for tab in config.get("tabs", []) if tab.get("key")]
        available = []
        for token in snapshot_tokens + web_tokens:
            if token and token not in available:
                available.append(token)
        saved = [token for token in config.get("order", []) if token in available]
        for token in available:
            if token not in saved:
                saved.append(token)
        return saved

    def _find_tab(self, key):
        for tab in self._config().get("tabs", []):
            if tab.get("key") == key:
                return tab
        return None

    def _normalize_url(self, value):
        url = (value or "").strip()
        if not url:
            return ""
        lower = url.lower()
        if not lower.startswith(("http://", "https://")):
            url = "https://" + url
        return url if url.lower().startswith(("http://", "https://")) else ""

    def _host_from_url(self, url):
        try:
            from urllib.parse import urlparse
            return urlparse(url).hostname or "WebView"
        except Exception:
            return "WebView"

    def _has_uitem_id(self, items, item_id):
        try:
            for i in range(items.size()):
                if int(items.get(i).id) == item_id:
                    return True
        except Exception:
            return False
        return False

    def _find_insert_after_plugins(self, items):
        try:
            for i in range(items.size()):
                if int(items.get(i).id) == 5:
                    return i + 1
        except Exception:
            pass
        return min(5, items.size())

    def _plugin_model(self):
        try:
            PluginsController = self._class_ref("com.exteragram.messenger.plugins.PluginsController")
            controller = PluginsController.getInstance()
            field = PluginsController.getDeclaredField("plugins")
            field.setAccessible(True)
            plugins = field.get(controller)
            return plugins.get(__id__)
        except Exception:
            return None

    def _dex_dir(self):
        return os.path.join(get_plugins_dir(), __id__)

    def _class_ref(self, name):
        try:
            ctx = jclass("org.telegram.messenger.ApplicationLoader").applicationContext
            loader = ctx.getClassLoader()
            if loader is not None:
                return loader.loadClass(name)
        except Exception:
            pass
        try:
            Class = jclass("java.lang.Class")
            return Class.forName(name)
        except Exception:
            pass
        try:
            cls = jclass(name)
            if hasattr(cls, "getDeclaredMethod"):
                return cls
            if hasattr(cls, "class_"):
                return cls.class_
        except Exception:
            return None
        return None

    def _make_read_only(self, path):
        try:
            os.chmod(path, 0o444)
            return
        except Exception:
            pass
        try:
            File = jclass("java.io.File")
            File(path).setReadOnly()
        except Exception:
            return

    def _sha256(self, path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 128), b""):
                h.update(chunk)
        return h.hexdigest()
