# ETG WebView Folders

Generic ExteraGram plugin for configurable Telegram folder tabs backed by sandboxed WebView pages.

This is a separate plugin and repository. It does not include a default MAX tab. Users create their own WebView folders from plugin settings.

## What it does

- Adds configurable WebView tabs to the Telegram folder tab bar.
- Adds a `Настройки WebView папок` entry in Telegram settings directly under the ExteraGram settings row.
- Lets users create, edit and delete website folders.
- Lets users reorder WebView folders and native Telegram folders with the same drag list pattern used by Pill Stack.
- Keeps WebView state cached while switching away, instead of reloading the page from scratch every time.
- Sandboxes WebView permissions: camera, microphone, geolocation and file chooser requests are denied.
- Allows only `http://` and `https://` navigation inside the embedded WebView.

## Files

- `etg_webview_folders.plugin` - plugin loader for ExteraGram.
- `plugin/etg_webview_folders.py` - loader source.
- `build/etg-webview-folders-bridge.dex` - Java bridge dex downloaded by the loader.
- `sources/com/etgwebfolders/bridge/WebFoldersBridge.java` - published Java bridge source.
- `dex/src/com/etgwebfolders/bridge/WebFoldersBridge.java` - build source.

## Build

```bash
./scripts/build_dex.sh
```

The build script needs Android SDK `android.jar`. If it cannot auto-detect it, set:

```bash
ANDROID_JAR=/path/to/android.jar ./scripts/build_dex.sh
```
