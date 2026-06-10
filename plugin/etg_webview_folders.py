import hashlib
import json
import os
import time
import urllib.request

from android_utils import run_on_ui_thread
from base_plugin import AppEvent, BasePlugin, MethodHook
from client_utils import PLUGINS_QUEUE, get_last_fragment, run_on_queue
from file_utils import ensure_dir_exists, get_plugins_dir
from java import dynamic_proxy, jclass
from java.lang import ClassLoader
from ui.settings import Divider, Header, Input, Text

__id__ = "etg_webview_folders"
__name__ = "WebView Folders"
__description__ = "Adds configurable Telegram folder tabs which open websites in a sandboxed WebView."
__author__ = "@bsod4ik_plugins"
__version__ = "1.0.3"
__icon__ = "msg_language"
__app_version__ = ">=12.5.1"
__sdk_version__ = ">=1.4.3.3"

ENTRY_CLASS = "com.etgwebfolders.bridge.WebFoldersBridge"
DEFAULT_DEX_URL = "https://raw.githubusercontent.com/nulls-brawl-site/etg-webview-folders/master/build/etg-webview-folders-bridge.dex"
DEFAULT_DEX_SHA256 = "310393137f3d6f394e005197723077f122d1550b1ff523d82ef2b5cab19256e9"
MAIN_PREFS_ITEM_ID = 0x575646
ORDER_ITEM_BASE_ID = 0x575700
CONFIG_KEY = "webview_folders_config"
ORDER_TITLE = "Порядок вкладок"


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
        self.plugin.inject_settings_item(param)


class _BeforeMainPrefsClick(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        if self.plugin.handle_settings_click(param):
            param.setResult(None)


class _AfterExteraPrefsFill(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        self.plugin.inject_extera_settings_item(param)


class _BeforeExteraPrefsClick(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        if self.plugin.handle_settings_click(param):
            param.setResult(None)


class _AfterPluginSettingsCreateView(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        self.plugin.enable_order_reorder(param.thisObject)


class _BeforePluginSettingsFill(MethodHook):
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        if self.plugin.inject_order_reorder_section(param):
            param.setResult(None)


class WebViewFoldersPlugin(BasePlugin):
    def on_plugin_load(self):
        self._bridge = None
        self._bridge_install = None
        self._bridge_before_update = None
        self._bridge_hide = None
        self._bridge_configure = None
        self._bridge_snapshot = None
        self._bridge_restore = None
        self._bridge_ready = False
        self._pending_fragments = []
        self._last_snapshot = {"tabs": []}
        self._last_bridge_config_json = None
        self._order_token_by_id = {}
        self._reorder_callbacks = []
        self._reorder_icon = None
        self._install_hooks()
        run_on_queue(self._load_bridge, PLUGINS_QUEUE)

    def on_plugin_unload(self):
        self._pending_fragments = []
        self.restore_chrome()

    def on_app_event(self, event_type: AppEvent):
        if event_type == AppEvent.RESUME:
            self._schedule_current_fragment_install()
        elif event_type in (AppEvent.PAUSE, AppEvent.STOP):
            self.restore_chrome()

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
                text=ORDER_TITLE,
                subtext="Перетаскивание как в Pill Stack",
                icon="msg_list",
                link_alias="webviewFoldersOrder",
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
        rows = [Header(text=ORDER_TITLE)]
        order_items = self._order_items()
        if order_items:
            for index, item in enumerate(order_items):
                rows.append(Text(
                    text=item.get("title") or "Папка",
                    subtext=item.get("subtext") or "",
                    icon=item.get("icon") or "msg_folders",
                    link_alias=f"webviewFoldersOrderRow{index}",
                ))
        else:
            rows.append(Text(
                text="Пока нечего двигать",
                subtext="Создай WebView папку или открой список чатов",
                icon="msg_info",
                link_alias="webviewFoldersOrderEmpty",
            ))
        return rows

    def _install_hooks(self):
        DialogsActivity = self._class_ref("org.telegram.ui.DialogsActivity")
        MainTabsActivity = self._class_ref("org.telegram.ui.MainTabsActivity")
        SettingsActivity = self._class_ref("org.telegram.ui.SettingsActivity")
        MainPreferencesActivity = self._class_ref("com.exteragram.messenger.preferences.MainPreferencesActivity")
        PluginSettingsActivity = self._class_ref("com.exteragram.messenger.plugins.ui.PluginSettingsActivity")
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

            if SettingsActivity is not None and ArrayList is not None and UniversalAdapter is not None:
                fill_items = SettingsActivity.getDeclaredMethod("fillItems", ArrayList, UniversalAdapter)
                fill_items.setAccessible(True)
                self.hook_method(fill_items, _AfterMainPrefsFill(self))

            if SettingsActivity is not None and UItem is not None and View is not None:
                on_click = SettingsActivity.getDeclaredMethod(
                    "onClick", UItem, View, Integer.TYPE, Float.TYPE, Float.TYPE
                )
                on_click.setAccessible(True)
                self.hook_method(on_click, _BeforeMainPrefsClick(self))

            if MainPreferencesActivity is not None and ArrayList is not None and UniversalAdapter is not None:
                extera_fill = MainPreferencesActivity.getDeclaredMethod("fillItems", ArrayList, UniversalAdapter)
                extera_fill.setAccessible(True)
                self.hook_method(extera_fill, _AfterExteraPrefsFill(self))

            if MainPreferencesActivity is not None and UItem is not None and View is not None:
                extera_click = MainPreferencesActivity.getDeclaredMethod(
                    "onClick", UItem, View, Integer.TYPE, Float.TYPE, Float.TYPE
                )
                extera_click.setAccessible(True)
                self.hook_method(extera_click, _BeforeExteraPrefsClick(self))

            if PluginSettingsActivity is not None and Context is not None:
                settings_create_view = PluginSettingsActivity.getDeclaredMethod("createView", Context)
                settings_create_view.setAccessible(True)
                self.hook_method(settings_create_view, _AfterPluginSettingsCreateView(self))

            if PluginSettingsActivity is not None and ArrayList is not None and UniversalAdapter is not None:
                settings_fill = PluginSettingsActivity.getDeclaredMethod("fillItems", ArrayList, UniversalAdapter)
                settings_fill.setAccessible(True)
                self.hook_method(settings_fill, _BeforePluginSettingsFill(self))
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
            self._bridge_restore = self._bridge.getDeclaredMethod("restoreChromeAndSystemBars")
            self._bridge_restore.setAccessible(True)
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

    def restore_chrome(self):
        if self._bridge_ready and self._bridge_restore is not None:
            try:
                self._bridge_restore.invoke(None)
            except Exception:
                return

    def inject_settings_item(self, param):
        try:
            items = param.args[0]
            if items is None or self._has_uitem_id(items, MAIN_PREFS_ITEM_ID):
                return
            item = self._create_settings_uitem()
            if item is None:
                return
            insert_at = self._find_insert_under_extera_settings(items)
            items.add(insert_at, item)
        except Exception:
            return

    def inject_extera_settings_item(self, param):
        try:
            items = param.args[0]
            if items is None or self._has_uitem_id(items, MAIN_PREFS_ITEM_ID):
                return
            item = self._create_extera_settings_uitem(param.thisObject)
            if item is None:
                return
            items.add(self._find_insert_in_extera_preferences(items), item)
        except Exception:
            return

    def _create_settings_uitem(self):
        try:
            SettingsFactory = self._class_ref("org.telegram.ui.SettingsActivity$SettingCell$Factory")
            RDrawable = self._class_ref("org.telegram.messenger.R$drawable")
            Integer = jclass("java.lang.Integer")
            CharSequence = self._class_ref("java.lang.CharSequence")
            icon_id = self._drawable_id(RDrawable, "msg_language")
            if SettingsFactory is not None and icon_id:
                method = SettingsFactory.getDeclaredMethod(
                    "of", Integer.TYPE, Integer.TYPE, Integer.TYPE, Integer.TYPE, CharSequence
                )
                method.setAccessible(True)
                return method.invoke(None, MAIN_PREFS_ITEM_ID, -0x17cfd0, -0x17cfd0, icon_id, "Настройки WebView папок")
            UItem = self._class_ref("org.telegram.ui.Components.UItem")
            as_button = UItem.getMethod("asButton", Integer.TYPE, Integer.TYPE, CharSequence)
            return as_button.invoke(None, MAIN_PREFS_ITEM_ID, icon_id, "Настройки WebView папок")
        except Exception:
            return None

    def _create_extera_settings_uitem(self, fragment):
        try:
            UItem = self._class_ref("org.telegram.ui.Components.UItem")
            RDrawable = self._class_ref("org.telegram.messenger.R$drawable")
            Integer = jclass("java.lang.Integer")
            CharSequence = self._class_ref("java.lang.CharSequence")
            icon_id = self._drawable_id(RDrawable, "msg_language") or self._drawable_id(RDrawable, "msg_settings")
            method = UItem.getMethod("asButton", Integer.TYPE, Integer.TYPE, CharSequence)
            item = method.invoke(None, MAIN_PREFS_ITEM_ID, icon_id, "Настройки WebView папок")
            try:
                item.setSearchable(fragment)
                item.setLinkAlias("webviewFolders", fragment)
            except Exception:
                pass
            return item
        except Exception:
            return None

    def handle_settings_click(self, param):
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

    def enable_order_reorder(self, settings_activity):
        if not self._is_order_settings_activity(settings_activity):
            return
        try:
            list_view = self._get_field(settings_activity, "listView")
            if list_view is None:
                return
            list_view.allowReorder(True)
            callback = self._make_reorder_callback()
            if callback is not None:
                self._reorder_callbacks.append(callback)
                if len(self._reorder_callbacks) > 8:
                    self._reorder_callbacks = self._reorder_callbacks[-8:]
                list_view.listenReorder(callback)
        except Exception:
            return

    def inject_order_reorder_section(self, param):
        settings_activity = param.thisObject
        if not self._is_order_settings_activity(settings_activity):
            return False
        try:
            items = param.args[0]
            adapter = param.args[1]
            if items is None or adapter is None:
                return False
            order_items = self._order_items()
            UItem = self._class_ref("org.telegram.ui.Components.UItem")
            if UItem is None:
                return False
            CharSequence = self._class_ref("java.lang.CharSequence")
            as_header = UItem.getMethod("asHeader", CharSequence)
            as_shadow = UItem.getMethod("asShadow", CharSequence)
            rows = []
            self._order_token_by_id = {}
            if order_items:
                for index, order_item in enumerate(order_items):
                    row_id = ORDER_ITEM_BASE_ID + index
                    row = self._create_order_uitem(settings_activity, row_id, order_item)
                    if row is not None:
                        rows.append(row)
                        self._order_token_by_id[row_id] = order_item["token"]
            if not rows and order_items:
                return False
            adapter.whiteSectionStart()
            items.add(as_header.invoke(None, ORDER_TITLE))
            if rows:
                adapter.reorderSectionStart()
                for row in rows:
                    items.add(row)
                adapter.reorderSectionEnd()
                adapter.whiteSectionEnd()
                items.add(as_shadow.invoke(None, "Перетащи строки за значок справа. Родные папки появятся после открытия списка чатов."))
            else:
                info = self._create_basic_uitem(
                    row_id=ORDER_ITEM_BASE_ID,
                    icon_name="msg_info",
                    title="Пока нечего двигать",
                    subtext="",
                )
                if info is not None:
                    items.add(info)
                adapter.whiteSectionEnd()
            return True
        except Exception:
            return False

    def _order_items(self):
        config = self._config()
        snapshot_by_token = {}
        for item in self._snapshot().get("tabs", []):
            token = item.get("token")
            if token:
                snapshot_by_token[token] = item
        tab_by_key = {
            tab.get("key"): tab
            for tab in config.get("tabs", [])
            if tab.get("key") and tab.get("enabled", True)
        }
        result = []
        for token in self._full_order(config):
            if token.startswith("web:"):
                tab = tab_by_key.get(token[4:])
                if not tab:
                    continue
                result.append({
                    "token": token,
                    "title": tab.get("title") or self._host_from_url(tab.get("url", "")),
                    "subtext": tab.get("url", "WebView"),
                    "icon": "msg_language",
                })
            else:
                item = snapshot_by_token.get(token)
                if not item:
                    continue
                result.append({
                    "token": token,
                    "title": item.get("title", token),
                    "subtext": "Telegram",
                    "icon": "msg_folders",
                })
        if not result:
            for tab in config.get("tabs", []):
                key = tab.get("key")
                if key and tab.get("enabled", True):
                    result.append({
                        "token": f"web:{key}",
                        "title": tab.get("title") or self._host_from_url(tab.get("url", "")),
                        "subtext": tab.get("url", "WebView"),
                        "icon": "msg_language",
                    })
        return result

    def _create_order_uitem(self, fragment, row_id, order_item):
        item = self._create_basic_uitem(
            row_id=row_id,
            icon_name=order_item.get("icon", "msg_folders"),
            title=order_item.get("title", ""),
            subtext="",
        )
        if item is None:
            return None
        self._set_field(item, "object", order_item.get("token", ""))
        reorder_icon = self._get_reorder_icon(fragment)
        if reorder_icon is not None:
            self._set_field(item, "object2", reorder_icon)
        return item

    def _create_basic_uitem(self, row_id, icon_name, title, subtext=""):
        try:
            UItem = self._class_ref("org.telegram.ui.Components.UItem")
            RDrawable = self._class_ref("org.telegram.messenger.R$drawable")
            Integer = jclass("java.lang.Integer")
            CharSequence = self._class_ref("java.lang.CharSequence")
            icon_id = self._drawable_id(RDrawable, icon_name)
            if not icon_id:
                icon_id = self._drawable_id(RDrawable, "msg_info")
            if subtext:
                method = UItem.getMethod("asButton", Integer.TYPE, Integer.TYPE, CharSequence, CharSequence)
                return method.invoke(None, row_id, icon_id, title, subtext)
            method = UItem.getMethod("asButton", Integer.TYPE, Integer.TYPE, CharSequence)
            return method.invoke(None, row_id, icon_id, title)
        except Exception:
            return None

    def _make_reorder_callback(self):
        try:
            try:
                Callback2 = jclass("org.telegram.messenger.Utilities$Callback2")
            except Exception:
                Callback2 = self._class_ref("org.telegram.messenger.Utilities$Callback2")
                if Callback2 is None:
                    return None
            plugin = self
            BaseCallback = dynamic_proxy(Callback2)

            class ReorderCallback(BaseCallback):
                def run(self, _section_id, item_list):
                    plugin._handle_order_reordered(item_list)

            return ReorderCallback()
        except Exception:
            return None

    def _handle_order_reordered(self, item_list):
        try:
            tokens = []
            for i in range(item_list.size()):
                item = item_list.get(i)
                token = self._get_field(item, "object")
                if token is None:
                    token = self._order_token_by_id.get(int(item.id))
                token = str(token) if token is not None else ""
                if token and token not in tokens:
                    tokens.append(token)
            if not tokens:
                return
            config = self._config()
            full_order = self._full_order(config)
            for token in full_order:
                if token not in tokens:
                    tokens.append(token)
            config["order"] = tokens
            self._save_config(config, reload_settings=False)
        except Exception:
            return

    def _is_order_settings_activity(self, settings_activity):
        if not self._is_our_plugin_settings_activity(settings_activity):
            return False
        title = str(self._get_field(settings_activity, "customTitle") or "")
        if title == ORDER_TITLE:
            return True
        prefix = str(self._get_field(settings_activity, "settingsLinkPrefix") or "")
        if "webviewFoldersOrder" in prefix:
            return True
        setting_items = self._get_field(settings_activity, "settingItems")
        try:
            if setting_items is not None:
                for i in range(setting_items.size()):
                    item = setting_items.get(i)
                    alias = str(self._get_field(item, "linkAlias") or "")
                    text = str(self._get_field(item, "text") or "")
                    if alias.startswith("webviewFoldersOrder") or text == ORDER_TITLE:
                        return True
        except Exception:
            pass
        return False

    def _is_our_plugin_settings_activity(self, settings_activity):
        try:
            plugin = self._get_field(settings_activity, "plugin")
            return plugin is not None and str(plugin.getId()) == __id__
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
            payload = self._config_json()
            changed = payload != self._last_bridge_config_json
            if changed:
                self._bridge_configure.invoke(None, payload)
                self._last_bridge_config_json = payload
            if reinstall and changed:
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
        web_tokens = [f"web:{tab.get('key')}" for tab in config.get("tabs", []) if tab.get("key") and tab.get("enabled", True)]
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

    def _find_insert_under_extera_settings(self, items):
        try:
            for i in range(items.size()):
                if int(items.get(i).id) == -1:
                    return i + 1
        except Exception:
            pass
        return min(1, items.size())

    def _find_insert_in_extera_preferences(self, items):
        try:
            for i in range(items.size()):
                text = str(self._get_field(items.get(i), "text") or "")
                if text in ("General", "Основные", "Общие"):
                    return i
        except Exception:
            pass
        try:
            return min(2, items.size())
        except Exception:
            return 0

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

    def _drawable_id(self, drawable_class, name):
        try:
            field = drawable_class.getDeclaredField(name)
            field.setAccessible(True)
            return field.getInt(None)
        except Exception:
            return 0

    def _get_reorder_icon(self, fragment):
        if self._reorder_icon is not None:
            return self._reorder_icon
        try:
            ContextCompat = self._class_ref("androidx.core.content.ContextCompat")
            RDrawable = self._class_ref("org.telegram.messenger.R$drawable")
            context = fragment.getContext()
            icon_id = self._drawable_id(RDrawable, "list_reorder")
            if ContextCompat is not None and context is not None and icon_id:
                self._reorder_icon = ContextCompat.getDrawable(context, icon_id)
        except Exception:
            self._reorder_icon = None
        return self._reorder_icon

    def _find_field(self, obj, name):
        try:
            cls = obj.getClass()
            while cls is not None:
                try:
                    field = cls.getDeclaredField(name)
                    field.setAccessible(True)
                    return field
                except Exception:
                    cls = cls.getSuperclass()
        except Exception:
            return None
        return None

    def _get_field(self, obj, name):
        try:
            field = self._find_field(obj, name)
            return field.get(obj) if field is not None else None
        except Exception:
            return None

    def _set_field(self, obj, name, value):
        try:
            field = self._find_field(obj, name)
            if field is not None:
                field.set(obj, value)
                return True
        except Exception:
            return False
        return False

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
