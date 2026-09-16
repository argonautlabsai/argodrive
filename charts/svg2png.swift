// Render an SVG chart to PNG with WebKit (ImageMagick and QuickLook mis-render our fonts).
// Build: DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swiftc -O -swift-version 5 \
//   -target arm64-apple-macosx14.0 -framework AppKit -framework WebKit -o svg2png svg2png.swift
// Use:   ./svg2png ladder.svg ladder.png 1000 494 2
import Cocoa
import WebKit
final class D: NSObject, WKNavigationDelegate {
    let w: Double, scale: Double, out: String
    init(w: Double, scale: Double, out: String) { self.w = w; self.scale = scale; self.out = out }
    func webView(_ v: WKWebView, didFinish n: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            let c = WKSnapshotConfiguration(); c.rect = v.bounds; c.snapshotWidth = NSNumber(value: self.w * self.scale)
            v.takeSnapshot(with: c) { img, err in
                guard let img = img, let t = img.tiffRepresentation, let r = NSBitmapImageRep(data: t),
                      let png = r.representation(using: .png, properties: [:]) else { fputs("snapshot failed: \(String(describing: err))\n", stderr); exit(1) }
                do { try png.write(to: URL(fileURLWithPath: self.out)) } catch { fputs("write failed\n", stderr); exit(1) }
                exit(0)
            }
        }
    }
}
let a = CommandLine.arguments
guard a.count >= 5, let w = Double(a[3]), let h = Double(a[4]) else { fputs("usage: svg2png in.svg out.png width height [scale]\n", stderr); exit(2) }
let scale = a.count > 5 ? (Double(a[5]) ?? 2) : 2
let app = NSApplication.shared; app.setActivationPolicy(.prohibited)
let web = WKWebView(frame: NSRect(x: 0, y: 0, width: w, height: h), configuration: WKWebViewConfiguration())
let win = NSWindow(contentRect: web.frame, styleMask: [.borderless], backing: .buffered, defer: false)
win.contentView = web
let d = D(w: w, scale: scale, out: a[2]); web.navigationDelegate = d
let u = URL(fileURLWithPath: a[1]); web.loadFileURL(u, allowingReadAccessTo: u.deletingLastPathComponent())
DispatchQueue.main.asyncAfter(deadline: .now() + 15) { fputs("timeout\n", stderr); exit(1) }
app.run()
