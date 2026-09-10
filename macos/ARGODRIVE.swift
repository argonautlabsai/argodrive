import AppKit
import WebKit
import Foundation

// The UI owns exactly one backend. It never adopts or stops an unrelated server.
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusLabel: NSTextField!
    private var progress: NSProgressIndicator!
    private var backend: Process?
    private var backendLog: FileHandle?
    private var readyFile: URL?
    private var serverURL: URL?
    private var startupTimer: Timer?
    private var startupDeadline = Date()
    private var live = false
    private var stopping = false
    private var generation = 0
    private var support: URL!
    private var logURL: URL!

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let override = ProcessInfo.processInfo.environment["ARGODRIVE_SUPPORT_DIR"]
            support = override.map { URL(fileURLWithPath: $0, isDirectory: true) } ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("ARGODRIVE", isDirectory: true)
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            logURL = support.appendingPathComponent("backend.log")
            buildMenus()
            let config = WKWebViewConfiguration()
            config.userContentController.add(self, name: "argodrive")
            webView = WKWebView(frame: .zero, configuration: config)
            webView.navigationDelegate = self
            window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "ARGODRIVE — Beta 1"
            window.minSize = NSSize(width: 760, height: 580)
            window.center()
            window.contentView = webView
            statusLabel = NSTextField(labelWithString: "Opening ARGODRIVE…")
            statusLabel.font = NSFont.systemFont(ofSize: 16, weight: .medium)
            statusLabel.textColor = .secondaryLabelColor
            statusLabel.translatesAutoresizingMaskIntoConstraints = false
            webView.addSubview(statusLabel)
            progress = NSProgressIndicator()
            progress.style = .spinning
            progress.translatesAutoresizingMaskIntoConstraints = false
            webView.addSubview(progress)
            NSLayoutConstraint.activate([
                statusLabel.centerXAnchor.constraint(equalTo: webView.centerXAnchor),
                statusLabel.centerYAnchor.constraint(equalTo: webView.centerYAnchor),
                progress.centerXAnchor.constraint(equalTo: statusLabel.centerXAnchor),
                progress.bottomAnchor.constraint(equalTo: statusLabel.topAnchor, constant: -18)
            ])
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            startBackend()
        } catch { showError("ARGODRIVE could not start", error.localizedDescription) }
    }

    private func menuItem(_ title: String, _ selector: Selector, _ key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        item.target = self
        return item
    }
    private func buildMenus() {
        let menu = NSMenu()
        let appItem = NSMenuItem(); let appMenu = NSMenu()
        appMenu.addItem(menuItem("About ARGODRIVE", #selector(about)))
        appMenu.addItem(.separator())
        appMenu.addItem(menuItem("Quit ARGODRIVE", #selector(quit), "q"))
        appItem.submenu = appMenu; menu.addItem(appItem)
        let fileItem = NSMenuItem(); fileItem.title = "File"; let file = NSMenu(title: "File")
        file.addItem(menuItem("Choose Run Folder…", #selector(chooseFolder), "o"))
        file.addItem(menuItem("Show Local Log", #selector(showLog)))
        file.addItem(menuItem("Reload Workspace", #selector(reload), "r"))
        fileItem.submenu = file; menu.addItem(fileItem)
        let editItem = NSMenuItem(); editItem.title = "Edit"; let edit = NSMenu(title: "Edit")
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            edit.addItem(NSMenuItem(title: title, action: Selector(action), keyEquivalent: key))
        }
        editItem.submenu = edit; menu.addItem(editItem)
        let monitorItem = NSMenuItem(); monitorItem.title = "Monitor"; let monitor = NSMenu(title: "Monitor")
        monitor.addItem(menuItem("Reports Only — Stop Collection", #selector(reportsMode)))
        monitor.addItem(menuItem("Live Hardware", #selector(liveMode)))
        monitorItem.submenu = monitor; menu.addItem(monitorItem)
        let windowItem = NSMenuItem(); windowItem.title = "Window"; let wm = NSMenu(title: "Window")
        wm.addItem(NSMenuItem(title: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        windowItem.submenu = wm; menu.addItem(windowItem); NSApp.windowsMenu = wm
        NSApp.mainMenu = menu
    }

    private func startBackend() {
        generation += 1
        let thisGeneration = generation
        stopping = false
        serverURL = nil
        webView.isHidden = false
        statusLabel.stringValue = live ? "Starting local hardware collection…" : "Opening your saved reports…"
        statusLabel.isHidden = false; progress.isHidden = false; progress.startAnimation(nil)
        guard let resources = Bundle.main.resourceURL else { showError("Incomplete app bundle", "The backend resources are missing."); return }
        let executable = resources.appendingPathComponent("backend/argodrive-server")
        readyFile = support.appendingPathComponent("ready-\(UUID().uuidString).json")
        do {
            // Keep one previous log; logs never contain a bundled user's benchmark data.
            if FileManager.default.fileExists(atPath: logURL.path) {
                let old = support.appendingPathComponent("backend.previous.log")
                try? FileManager.default.removeItem(at: old)
                try? FileManager.default.moveItem(at: logURL, to: old)
            }
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
            backendLog = try FileHandle(forWritingTo: logURL)
            let process = Process()
            process.executableURL = executable
            process.arguments = ["--port", "0", "--state-dir", support.path, "--ready-file", readyFile!.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)] + (live ? [] : ["--reports-only"])
            // A Finder launch has a small PATH. All required system tools live here.
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
            env["PYTHONUNBUFFERED"] = "1"
            process.environment = env
            process.standardOutput = backendLog; process.standardError = backendLog
            process.terminationHandler = { [weak self] process in
                DispatchQueue.main.async {
                    guard let self, self.generation == thisGeneration, !self.stopping else { return }
                    self.startupTimer?.invalidate()
                    self.showError("The local backend stopped", "Exit code \(process.terminationStatus). Use File → Show Local Log for details. No inference engine was started or stopped.")
                }
            }
            backend = process
            try process.run()
            startupDeadline = Date().addingTimeInterval(35)
            startupTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in self?.checkReady() }
        } catch { showError("The backend could not launch", error.localizedDescription) }
    }
    private func checkReady() {
        if let readyFile, let bytes = try? Data(contentsOf: readyFile), let obj = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
           let port = obj["port"] as? Int, let pid = obj["pid"] as? Int, pid == Int(backend?.processIdentifier ?? -1), (1024...65535).contains(port) {
            startupTimer?.invalidate(); startupTimer = nil
            try? FileManager.default.removeItem(at: readyFile)
            serverURL = URL(string: "http://127.0.0.1:\(port)/")!
            webView.load(URLRequest(url: serverURL!))
        } else if Date() > startupDeadline {
            stopBackend()
            showError("Startup timed out", "Use File → Show Local Log to inspect the problem, then File → Reload Workspace to retry.")
        }
    }
    private func stopBackend() {
        stopping = true
        startupTimer?.invalidate(); startupTimer = nil
        if let process = backend, process.isRunning {
            process.terminate()
            let deadline = Date().addingTimeInterval(5)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        }
        backend = nil
        try? backendLog?.close(); backendLog = nil
        if let readyFile { try? FileManager.default.removeItem(at: readyFile) }
    }
    private func local(_ url: URL?) -> Bool {
        guard let url, let serverURL else { return false }
        return url.scheme == serverURL.scheme && url.host == serverURL.host && url.port == serverURL.port
    }
    @objc private func chooseFolder() {
        guard serverURL != nil else { return }
        let picker = NSOpenPanel()
        picker.title = "Choose a folder containing ARGODRIVE run folders"
        picker.prompt = "Use Folder"
        picker.canChooseFiles = false; picker.canChooseDirectories = true; picker.allowsMultipleSelection = false
        picker.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let self, let path = picker.url?.path,
                  let data = try? JSONSerialization.data(withJSONObject: path, options: .fragmentsAllowed), let encoded = String(data: data, encoding: .utf8) else { return }
            self.webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('argodrive-folder',{detail:\(encoded)}))")
        }
    }
    @objc private func reportsMode() { if live { live = false; stopBackend(); startBackend() } }
    @objc private func liveMode() { if !live { live = true; stopBackend(); startBackend() } }
    @objc private func reload() { if backend?.isRunning == true { webView.reload() } else { startBackend() } }
    @objc private func showLog() { if let logURL { NSWorkspace.shared.activateFileViewerSelecting([logURL]) } }
    @objc private func quit() { NSApp.terminate(nil) }
    @objc private func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "ARGODRIVE", .applicationVersion: "0.2.0 Beta 1 · technical preview", .credits: NSAttributedString(string: "Local AI performance workspace.\nThis build is ad-hoc signed and has not been notarized.\nRDMA and clustering are planned capabilities.")])
    }
    private func showError(_ title: String, _ message: String) {
        progress?.stopAnimation(nil); progress?.isHidden = true
        statusLabel?.stringValue = title; statusLabel?.isHidden = false
        let alert = NSAlert(); alert.messageText = title; alert.informativeText = message
        alert.addButton(withTitle: "OK")
        if let window { alert.beginSheetModal(for: window) } else { alert.runModal() }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { stopBackend() }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, local(message.frameInfo.request.url), let body = message.body as? [String: String] else { return }
        if body["action"] == "chooseFolder" { chooseFolder() }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { statusLabel.isHidden = true; progress.stopAnimation(nil); progress.isHidden = true }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { showError("The workspace could not load", error.localizedDescription) }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if local(url) || url.scheme == "blob" {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            if action.navigationType == .linkActivated && ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let r = response.response as? HTTPURLResponse, r.value(forHTTPHeaderField: "Content-Disposition")?.contains("attachment") == true { decisionHandler(.download) }
        else { decisionHandler(response.canShowMIMEType ? .allow : .download) }
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { showError("Export did not finish", error.localizedDescription) }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
