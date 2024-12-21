# Launcher for Invoke Community Edition

The launcher is a desktop application for Windows, macOS (Apple Silicon) and Linux.

It can install, update, reinstall and run Invoke Community Edition. It is self-contained, so you don't need to worry about having the right python version installed.

Download the launcher from the [releases](https://github.com/invoke-ai/launcher/releases) page.

<img width="912" alt="image" src="https://github.com/user-attachments/assets/cd676db3-b8e6-4dcc-b7a9-e442ed98a616" />

## FAQ

### Using the launcher to update Invoke

You can install the latest stable release or latest prerelease versions of Invoke using the launcher.

### Updating the launcher itself

> Note: This section discusses updating launcher itself, _not_ updating the Invoke application.

Currently, the launcher is not able to auto-update itself, but we are working on this now.

### 🚨 macOS: security warning / unable to run

On macOS, the launcher may require you to go to **System Settings** > **Security and Privacy** and manually allow it to run. It may tell you that it is damaged and not run at all.

This is related to Apple's strict code signing restrictions. We are getting set up with Apple to sign the launcher executable, which will fix the issue and allow the launcher to run on all Macs without any fuss.

Until this is resolved, you can manually flag the launcher as safe with `xattr` and it will run.:

```zsh
xattr -d 'com.apple.quarantine' /Applications/Invoke-Installer.app
```

### Compared to the "old" install/invoke scripts

Like the install script, the launcher creates and manages a normal python virtual environment.

> We suggest leaving the `venv` alone and letting the launcher manage it, but you can interact with it like you would any `venv` if you need to.

Unlike the invoke script, the launcher provides a GUI to run the app.

### What happens to the "old" install/invoke scripts?

The "old" scripts will be phased out over time. The goal is to support 3 ways to install and run Invoke:

- Launcher
- Docker
- Manual (e.g. create a `venv` manually, install the `invokeai` package, run it as a script)

### Can I still use the "old" install/invoke scripts?

Yes, but we won't keep them updated for too much longer. Theoretically, they should continue to work for some time, even without updates.

## Technical Details

The launcher is an [electron](https://github.com/electron/electron) application with [React](https://github.com/facebook/react) UI. We bundle [`uv`](https://github.com/astral-sh/uv) with the build and then call it to install python, create the app `venv`, and run the app.

### Why Electron?

There are a number of lighter-weight systems that enable cross-platform builds. [Tauri](https://tauri.app/) is probably the most popular, but there are others.

These other systems use the OS-provided engine to render their UIs. That means on Windows uses WebView2 (Chromium), macOS uses WebKit (Safari), and Linux uses WebKitGTK (basically a Linux port of Safari), and the version of the engine depends on the computer.

The result is an inconsistent user experience, and increased workload for devs to support the various rendering engines.

Electron uses the same version of Chrome for all platforms. We only need to build for one rendering engine target, and we can be far more confident in a consistent, bug-free application.

Electron uses about 10x more disk space than something like Tauri, but we're still only talking ~150MB max. You are going to install _many_ GB of models, right? The extra disk usage is a drop in the bucket and both devs and users have a much better experience.

## Dev Setup

This project uses node 22 and npm as its package manager.

- Run `npm i` to install all packages. See the next section to get `uv` set up.
- Run `npm run start` to start the launcher in dev mode.
- Run `npm app:dist` to build it for your system.

### Getting `uv` for local dev

The GitHub CI build workflow downloads `uv` during the build step, but you'll need to download it manually to run the launcher in dev mode.

- Check `.github/workflows/build.yml` for the version of `uv` used in the build.
- Download that version from [`uv`'s releases page on GH](https://github.com/astral-sh/uv/releases).
- Move the `uv` binary to `assets/`, which mirrors the file structure expected by the build process.

On macOS, you may need to remove the quarantine flag from the `uv` binary:

```sh
xattr -d 'com.apple.quarantine' assets/uv`
```

### Code Signing

This repo does not contain the necessary secrets to do code signing. We handle that separately.

As a result, the builds from this repo may require you to manually allow them to run on Windows and macOS.

Windows will prompt you to allow the app to run, but on macOS it may refuse, saying the app is damaged. Remove the quarantine flag from the `.app` package to resolve this:

```zsh
xattr -d 'com.apple.quarantine' /Applications/Invoke-Installer.app
```
